import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const apiTarget = process.env.FORGE_TEST_API_URL || 'http://127.0.0.1:8000';
export default defineConfig({
 plugins:[react()],
 optimizeDeps:{entries:['index.html']},
 server:{
  watch:{ignored:['**/.forge/**','**/.venv/**','**/backend/**','**/test-results/**','**/playwright-report/**']},
  proxy:{'/v1':apiTarget,'/health':apiTarget}
 }
});
