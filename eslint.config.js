import parser from '@typescript-eslint/parser';
import hooks from 'eslint-plugin-react-hooks';

export default [
 {ignores:['src/generated/**','src/icons.tsx','dist/**','.forge/**','node_modules/**']},
 {files:['src/**/*.{ts,tsx}'],languageOptions:{parser,parserOptions:{ecmaVersion:'latest',sourceType:'module',ecmaFeatures:{jsx:true}}},plugins:{'react-hooks':hooks},rules:{
  'react-hooks/rules-of-hooks':'error',
  'no-async-promise-executor':'error','no-unreachable':'error','no-unsafe-finally':'error',
  'no-constant-condition':'error','no-dupe-args':'error','no-duplicate-case':'error'
 }}
];
