"""One structured Claude call, with model profiles and cost accounting."""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

# (model, effort) per profile. `dev` is the default so routine runs stay cheap; publish numbers
# from `report`. REVIEW_LOOP_MODEL and REVIEW_LOOP_EFFORT override either part.
PROFILES: dict[str, tuple[str, str]] = {
    "dev": ("claude-haiku-5-5", "low"),
    "report": ("claude-opus-5-5", "medium"),
}
# Models that accept server-side refusal fallback (`fallbacks: "default"`). Haiku 5.5 has none.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
# USD per million tokens (input, output), Claude API pricing table (2026-10-06).
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-4-8": (5.00, 25.00),
}


class LLMError(RuntimeError):
    pass


@dataclass
class Spend:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    models: set[str] = field(default_factory=set)


class LLM(Protocol):
    spend: Spend

    def call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def resolve(prefix: str = "REVIEW_LOOP") -> tuple[str, str]:
    """`<prefix>_PROFILE` picks (model, effort); `<prefix>_MODEL` / `_EFFORT` override either."""
    profile = os.environ.get(f"{prefix}_PROFILE", "dev")
    if profile not in PROFILES:
        raise SystemExit(f"{prefix}_PROFILE must be one of {sorted(PROFILES)}, got {profile!r}")
    model, effort = PROFILES[profile]
    return os.environ.get(f"{prefix}_MODEL", model), os.environ.get(f"{prefix}_EFFORT", effort)


class Claude:
    def __init__(self, client: Any | None = None, env_prefix: str = "REVIEW_LOOP") -> None:
        import anthropic

        self.client = client or anthropic.Anthropic()
        self.model, self.effort = resolve(env_prefix)
        self.spend = Spend()

    def call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        import anthropic

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        if self.model in FALLBACK_MODELS:
            params |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc
        usage = response.usage
        price = PRICES.get(response.model) or PRICES[self.model]
        self.spend.input_tokens += usage.input_tokens
        self.spend.output_tokens += usage.output_tokens
        self.spend.cost_usd += (
            usage.input_tokens * price[0] + usage.output_tokens * price[1]
        ) / 1e6
        self.spend.models.add(response.model)
        if response.stop_reason == "refusal":
            raise LLMError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("response hit max_tokens before finishing")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"response was not valid JSON: {exc}") from exc
        return data


# Claude Code bills an API key when it finds one; without one it uses the claude.ai login.
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# Without this, Claude Code resends the whole prompt uncached for a side request.
ADDED_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
CLAUDE_TIMEOUT_S = 600


class ClaudeCode:
    """The same call through headless Claude Code (`claude -p`), billed to a Claude login.

    `--safe-mode` drops every customization (CLAUDE.md, skills, plugins, hooks, MCP servers),
    and there are no tools. Claude Code adds a short preamble and has no refusal fallback;
    its cost is a list-price estimate a subscription doesn't bill.
    """

    def __init__(self, env_prefix: str = "REVIEW_LOOP", binary: str | None = None) -> None:
        self.model, self.effort = resolve(env_prefix)
        self.binary = binary or os.environ.get("REVIEW_LOOP_CLAUDE", "claude")
        self.spend = Spend()

    def call(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        import subprocess
        import tempfile
        from pathlib import Path

        env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV} | ADDED_ENV
        with tempfile.TemporaryDirectory(prefix="review-loop-") as tmp:
            system_file = Path(tmp) / "system.md"
            system_file.write_text(system)
            command = [
                self.binary, "-p",
                "--model", self.model,
                "--effort", self.effort,
                "--output-format", "json",
                "--json-schema", json.dumps(schema),
                "--system-prompt-file", str(system_file),
                "--tools", "",
                "--safe-mode",
                "--setting-sources", "",
                "--strict-mcp-config",
                "--no-session-persistence",
            ]  # fmt: skip
            try:
                done = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=tmp,
                    timeout=CLAUDE_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"claude -p timed out after {CLAUDE_TIMEOUT_S}s") from exc
        try:
            result: dict[str, Any] = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            detail = (done.stderr or done.stdout).strip()[-300:]
            raise LLMError(f"claude -p printed no JSON result: {detail}") from exc
        for model, usage in (result.get("modelUsage") or {}).items():
            cached = usage.get("cacheReadInputTokens", 0) + usage.get("cacheCreationInputTokens", 0)
            self.spend.input_tokens += usage.get("inputTokens", 0) + cached
            self.spend.output_tokens += usage.get("outputTokens", 0)
            self.spend.cost_usd += usage.get("costUSD", 0.0)
            self.spend.models.add(model)
        if result.get("stop_reason") == "refusal":
            raise LLMError("the model declined the request")
        if result.get("is_error") or result.get("subtype") != "success":
            raise LLMError(f"claude -p {result.get('subtype')}: {str(result.get('result'))[:300]}")
        data = result.get("structured_output")
        if not isinstance(data, dict):
            raise LLMError("claude -p returned no structured output")
        return data


def make_llm(env_prefix: str = "REVIEW_LOOP") -> "Claude | ClaudeCode":
    """REVIEW_LOOP_TRANSPORT picks who bills: `api` (the default, and CI's) or `claude-code`."""
    transport = os.environ.get("REVIEW_LOOP_TRANSPORT", "api")
    if transport == "claude-code":
        return ClaudeCode(env_prefix)
    if transport != "api":
        raise SystemExit(f"REVIEW_LOOP_TRANSPORT must be api or claude-code, got {transport!r}")
    return Claude(env_prefix=env_prefix)
