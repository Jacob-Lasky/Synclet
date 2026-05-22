// Flat config. ESLint v9+ syntax.
//
// Curated rule set, NOT exhaustive. The goal here is the same as the backend
// ruff config: catch real bugs and contract drift, enforce a single format,
// avoid noise that fights idiomatic Vue 3 + TypeScript code. If a rule shows
// up as the firehose against this codebase, the answer is to drop the rule,
// not blanket-disable callers.

const vue = require("eslint-plugin-vue")
const tsPlugin = require("@typescript-eslint/eslint-plugin")
const tsParser = require("@typescript-eslint/parser")
const vueParser = require("vue-eslint-parser")
const prettierRecommended = require("eslint-plugin-prettier/recommended")

const config = [
    {
        ignores: [
            "node_modules/",
            "dist/",
            "public/",
            "*.config.js",
            "vite.config.ts",
            "vitest.config.ts",
        ],
    },

    // Vue + TypeScript sources. Type-aware lint requires tsconfig.eslint.json.
    ...vue.configs["flat/recommended"],
    {
        files: ["**/*.ts", "**/*.vue"],
        languageOptions: {
            parser: vueParser,
            parserOptions: {
                parser: tsParser,
                ecmaVersion: 2021,
                sourceType: "module",
                project: "./tsconfig.eslint.json",
                tsconfigRootDir: __dirname,
                extraFileExtensions: [".vue"],
            },
        },
        plugins: {
            "@typescript-eslint": tsPlugin,
        },
        rules: {
            ...tsPlugin.configs.recommended.rules,

            // Type-safety rules that catch real footguns in TS+Vue glue code.
            // Each chosen because it would have caught a class of bug we've
            // hit in the surrounding codebases, not for completeness.
            "@typescript-eslint/no-unsafe-member-access": "error",
            "@typescript-eslint/no-unsafe-assignment": "warn",
            "@typescript-eslint/no-unsafe-call": "warn",
            "@typescript-eslint/no-unsafe-return": "error",
            "@typescript-eslint/no-unsafe-argument": "error",
            "@typescript-eslint/no-explicit-any": "error",
            "@typescript-eslint/no-unnecessary-type-assertion": "error",
            "@typescript-eslint/prefer-as-const": "error",
            "@typescript-eslint/no-non-null-assertion": "error",
            "@typescript-eslint/no-misused-new": "error",
            "@typescript-eslint/no-this-alias": "error",
            "@typescript-eslint/prefer-readonly": "error",

            // unused-vars: allow `_` prefix to silence (matches Vue conventions
            // around destructured props the template doesn't reference).
            "@typescript-eslint/no-unused-vars": [
                "error",
                {
                    argsIgnorePattern: "^_",
                    varsIgnorePattern: "^_",
                    caughtErrorsIgnorePattern: "^_",
                },
            ],

            // Vue refinements that fire in idiomatic Vue 3 SFCs.
            // The recommended set wants kebab-case events and a few component
            // formatting rules that prettier already owns. Trim the overlap.
            "vue/multi-word-component-names": "off",
        },
    },

    // Test files: relax the non-null assertion ban. Vitest's idiomatic pattern
    // is `expect(x).toBeDefined()` followed by `x!.foo` — the assertion has
    // already narrowed runtime, the `!` just shuts up TS. Forcing a second
    // narrowing layer (`if (!x) throw`) is noise in test code.
    {
        files: ["**/*.test.ts"],
        rules: {
            "@typescript-eslint/no-non-null-assertion": "off",
        },
    },

    // Loose JS/CJS files (this config + any future scripts). No type-aware
    // rules, since TS-eslint without parser metadata throws.
    {
        files: ["**/*.js", "**/*.cjs"],
        languageOptions: {
            ecmaVersion: 2021,
            sourceType: "commonjs",
            globals: {
                console: "readonly",
                module: "readonly",
                require: "readonly",
                __dirname: "readonly",
                process: "readonly",
            },
        },
    },

    // Prettier last so it wins formatting conflicts. The /recommended bundle
    // also turns off eslint formatting rules that overlap with prettier.
    prettierRecommended,
]

module.exports = config
