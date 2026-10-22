import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // The website demo is served from sochub.io/demo/
  base: mode === 'demo' ? '/demo/' : '/',
}))
