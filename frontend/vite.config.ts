import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: Number(process.env.DFIR_UI_PORT ?? 5180),
    strictPort: true,
    proxy: { '/api': { target: process.env.DFIR_API ?? 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
