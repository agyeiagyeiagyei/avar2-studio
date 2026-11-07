import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import tsParser from '@typescript-eslint/parser';
import globals from 'globals';

export default [
  {
    ignores: ['build/', 'node_modules/'],
  },
  js.configs.recommended,
  {
    files: ['src/**/*.{js,jsx,ts,tsx}'],
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: globals.browser,
    },
    settings: {
      react: { version: 'detect' },
    },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs['recommended-latest'].rules,
      // TS components carry prop interfaces instead of prop-types.
      'react/prop-types': 'off',
      // Warnings, not errors, like CRA's react-app config: the existing
      // components predate this config and must stay untouched.
      'no-unused-vars': 'warn',
      'react/no-unescaped-entities': 'warn',
      // A dangling `+ (cond ? 1 : 0);` line survived a refactor in Header.tsx
      // (A1): eslint:recommended does not include that rule, and tsc accepts a
      // stray expression statement. Short-circuit and ternary expressions stay
      // legal, because `cond && doThing()` is idiomatic React.
      'no-unused-expressions': ['error', {
        allowShortCircuit: true,
        allowTernary: true,
        allowTaggedTemplates: true,
      }],
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
    },
    rules: {
      // TypeScript already checks these; the core rules false-positive
      // on type-only usage. tsc --noEmit is the real bar for .ts/.tsx.
      'no-unused-vars': 'off',
      'no-undef': 'off',
    },
  },
  {
    // Playwright specs and their harness scripts run in Node AND evaluate
    // browser code (page.evaluate bodies reference window/document).
    files: ['e2e/**/*.mjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.node, ...globals.browser },
    },
  },
];
