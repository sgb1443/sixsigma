import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' keeps asset paths relative, so the page works at any URL (Posit Connect too).
// The build goes straight into app/static, which FastAPI serves at "/".
const api = 'http://localhost:8000'
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: '../app/static', emptyOutDir: true },
  server: {
    proxy: Object.fromEntries(
      ['/health', '/fridges', '/generators', '/attention', '/log'].map((p) => [p, api])
    ),
  },
})
