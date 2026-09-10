import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

const apiPort = loadEnv('production', '.', 'ASTRA_').ASTRA_LOCAL_API_PORT || '8000';

export default defineConfig({
  plugins: [react()],
  // 离线服务 LIRIS 三维资产：assets/tunnel/liris/* → /tunnel/liris/*
  // （build 时同步拷贝进 dist）
  publicDir: '../../assets',
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: false,
      },
      '/ws': {
        target: `ws://127.0.0.1:${apiPort}`,
        ws: true,
        changeOrigin: false,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false },
      '/ws': { target: `ws://127.0.0.1:${apiPort}`, ws: true, changeOrigin: false },
    },
  },
});
