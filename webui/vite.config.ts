import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// Served by Flask at /app/ in production; the dev server proxies /api to a
// locally running bot (interface.py) so `npm run dev` works against real data.
export default defineConfig({
  base: './',
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8181',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
