import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = `http://127.0.0.1:${process.env.METADATA_API_PORT || '8000'}`;

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': backend, '/docs': backend, '/redoc': backend, '/openapi.json': backend } },
});
