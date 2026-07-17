import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The frontend calls the FastAPI backend directly at :8000 (CORS is open there).
// Override with VITE_API_BASE if the backend runs elsewhere.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
})
