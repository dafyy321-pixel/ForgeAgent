export const apiURL = (process.env.FORGE_TEST_API_URL || 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
);
