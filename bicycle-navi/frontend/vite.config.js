import fs from 'fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: 'all',
    https: {
      key: fs.readFileSync('./localhost+1-key.pem'),
      cert: fs.readFileSync('./localhost+1.pem'),
    },
    // LAN公開時（npm run dev -- --host）でも /api をバックエンドに中継する
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
