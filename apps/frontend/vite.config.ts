import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// A browser running on the host reaches a local API at localhost, while Vite
// running in the development Compose container must reach the `api` service.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000';

export default defineConfig({plugins:[react()],server:{host:true,proxy:{'/api':apiProxyTarget}},build:{rollupOptions:{output:{manualChunks:{react:['react','react-dom','react-router-dom','@tanstack/react-query'],workflow:['@xyflow/react'],charts:['recharts']}}}},test:{environment:'jsdom',setupFiles:'./src/test-setup.ts'}});
