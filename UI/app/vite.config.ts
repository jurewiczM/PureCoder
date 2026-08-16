import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The API is loopback-only by deliberate design -- `purecoder serve` runs
// model-authored code in a subprocess on this machine, so it refuses to bind
// anything but 127.0.0.1. Proxying through the dev server keeps it that way:
// the browser talks to Vite, Vite talks to the pipeline, and nothing needs a
// CORS header or a second listening port.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5273,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // A generation is minutes. The default proxy timeout would cut the
        // event stream off mid-run and the UI would report a failure the
        // pipeline never had.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
})
