// Template: the lint half of the playwright-testing skill. Copy into a repo's test folder,
// install eslint, typescript-eslint, and eslint-plugin-playwright, and adjust the paths and the
// account-email pattern. Rule IDs in messages (PW-n) match SKILL.md.
import playwright from "eslint-plugin-playwright";
import tseslint from "typescript-eslint";

const sharedStepsInSpecs = [
  "Program > FunctionDeclaration",
  "Program > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression",
  "Program > VariableDeclaration > VariableDeclarator > FunctionExpression",
].map((selector) => ({
  selector,
  message: "PW-1: spec files hold tests only. Put shared steps in fixtures/ (factory for data, a page object for UI).",
}));

const noCredentials = [
  {
    selector: "Literal[value=/@[a-z0-9.-]+\\.test$/]",
    message: "PW-4: no account emails in specs. Use a persona: apiAs/signIn/pageAs, or USERS from fixtures.",
  },
  {
    selector: "Literal[value='demo-password']",
    message: "PW-4: no passwords in specs. Use PASSWORD from fixtures, or a persona fixture.",
  },
];

const statusWithBody = {
  selector: "CallExpression[callee.name='expect'] > CallExpression.arguments[callee.property.name='status'][arguments.length=0]",
  message: "PW-9: use expectStatus(response, code) so a failure shows the response body.",
};

const oneImport = [
  "error",
  {
    paths: [
      { name: "@playwright/test", message: "PW-6: import test, expect, and types from ../fixtures, so the fixtures come with them." },
      { name: "@axe-core/playwright", message: "PW-6: use the a11y fixture." },
    ],
  },
];

const noInlineTypeImports = {
  selector: "TSImportType",
  message: "PW-6: import types from ../fixtures instead of inline import(\"...\") types.",
};

export default tseslint.config(
  { ignores: ["node_modules/**", "playwright-report/**", "test-results/**"] },
  {
    files: ["**/*.ts"],
    extends: [tseslint.configs.base],
  },
  {
    ...playwright.configs["flat/recommended"],
    files: ["api/**/*.ts", "ui/**/*.ts", "fixtures/**/*.ts"],
    rules: {
      ...playwright.configs["flat/recommended"].rules,
      // PW-5: no fixed waits; assert on the UI and let Playwright retry.
      "playwright/no-wait-for-timeout": "error",
      "playwright/no-wait-for-selector": "error",
      "playwright/no-networkidle": "error",
      "playwright/prefer-web-first-assertions": "error",
      "playwright/no-force-option": "error",
      "playwright/no-element-handle": "error",
      "playwright/no-eval": "error",
      // PW-7: role, label, and text locators only.
      "playwright/no-raw-locators": "error",
      // PW-8: every assertion runs on every path.
      "playwright/no-conditional-expect": "error",
      "playwright/no-conditional-in-test": "error",
      "playwright/no-skipped-test": "error",
      "playwright/no-focused-test": "error",
      "playwright/missing-playwright-await": "error",
    },
  },
  {
    files: ["api/**/*.spec.ts"],
    rules: {
      "no-restricted-syntax": ["error", ...sharedStepsInSpecs, ...noCredentials, noInlineTypeImports, statusWithBody],
      "no-restricted-imports": oneImport,
    },
  },
  {
    files: ["ui/**/*.spec.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        ...sharedStepsInSpecs,
        ...noCredentials,
        noInlineTypeImports,
        statusWithBody,
        {
          selector: "Literal[value='/api/auth/login']",
          message: "PW-4: UI specs sign in with signIn() or pageAs(), not by posting to the login endpoint.",
        },
      ],
      "no-restricted-imports": oneImport,
    },
  },
);
