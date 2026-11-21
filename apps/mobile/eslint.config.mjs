import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import hooks from 'eslint-plugin-react-hooks';
import prettier from 'eslint-config-prettier';
import globals from 'globals';
export default tseslint.config(
  { ignores: ['node_modules/**', '.expo/**', 'dist/**', 'android/**', 'ios/**', 'coverage/**'] },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    languageOptions: {
      globals: { ...globals.node, ...globals.jest, ...globals.browser, __DEV__: 'readonly' },
    },
    plugins: { 'react-hooks': hooks },
    rules: hooks.configs.recommended.rules,
  },
  {
    files: ['**/*.cjs', '**/__mocks__/**', 'scripts/**'],
    rules: { '@typescript-eslint/no-require-imports': 'off' },
  },
  prettier,
);
