import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':'http://localhost:8000'}},build:{rollupOptions:{output:{manualChunks:{react:['react','react-dom','react-router-dom','@tanstack/react-query'],workflow:['@xyflow/react'],charts:['recharts']}}}},test:{environment:'jsdom',setupFiles:'./src/test-setup.ts'}});
