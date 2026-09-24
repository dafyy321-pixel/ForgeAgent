import { defineConfig } from '@playwright/test';
export default defineConfig({
 testDir:'./tests/frontend',timeout:45000,expect:{timeout:8000},fullyParallel:true,workers:2,
 reporter:[['list'],['html',{open:'never'}]],
 use:{baseURL:'http://127.0.0.1:5173',viewport:{width:1440,height:1000},launchOptions:{executablePath:process.env.FORGE_BROWSER_PATH},screenshot:'only-on-failure',trace:'retain-on-failure'},
 webServer:{command:'npm run dev -- --port 5173',url:'http://127.0.0.1:5173',reuseExistingServer:true,timeout:30000}
});
