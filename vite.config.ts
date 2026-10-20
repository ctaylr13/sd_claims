import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import wyw from '@wyw-in-js/vite'

export default defineConfig({
  plugins: [
    wyw({ include: ['**/*.{ts,tsx}'] }),
    react(),
  ],
})