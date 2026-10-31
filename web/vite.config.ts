/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';
import { cloudflare } from '@cloudflare/vite-plugin';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://localhost:8000';

  return {
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    plugins: [react(), tailwindcss(), ...(mode === 'cloudflare' ? cloudflare() : [])],
    define: mode === 'cloudflare' ? { 'import.meta.env.VITE_USE_FIXTURES': '"true"' } : {},
    server: {
      port: 5173,
      proxy: {
        '/api': { target: proxyTarget, changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      // Use jsdom storage, not Node 25+ file-backed experimental Web Storage.
      execArgv: ['--no-experimental-webstorage'],
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      // Component tests run against the JSON fixtures, never a live backend.
      env: { VITE_USE_FIXTURES: 'true' },
    },
  };
});
