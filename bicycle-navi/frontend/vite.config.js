import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'
  
// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    // LAN公開時（npm run dev -- --host）でも /api をバックエンドに中継する
    // これにより、スマホからアクセスしても CORS 問題が起きない
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
