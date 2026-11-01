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
