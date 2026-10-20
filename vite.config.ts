import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import wyw from '@wyw-in-js/vite'

export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  plugins: [
    wyw({ include: ['**/*.{ts,tsx}'] }),
    react(),
  ],
})
