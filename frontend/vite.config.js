import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  // Flask serves the bundle under /static/ (server.py's /static/<path>
  // route) — without this, Vite's default /assets/ URLs 404 and the app
  // is a white page.
  base: '/static/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      // Mirrors CRA's src/setupProxy.js (http-proxy-middleware):
      // target http://localhost:5001, changeOrigin, secure false.
      // Its `logLevel: 'debug'` has no Vite equivalent and is dropped.
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    // Keep CRA's output dir so the deploy command keeps working:
    //   rsync -a --delete build/ ../src/avar2_studio/static/
    outDir: 'build',
  },
});
