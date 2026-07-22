import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// Same-origin in production (CloudFront serves the SPA and proxies /api/*). For local `vite dev`
// there is no CloudFront, so optionally proxy /api to a real backend — set VITE_DEV_API_PROXY to
// the sandbox CloudFront URL (or a local API). Unset → no proxy (calls will 404 locally).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTarget = env.VITE_DEV_API_PROXY;
  return {
    plugins: [react()],
    server: proxyTarget
      ? { proxy: { '/api': { target: proxyTarget, changeOrigin: true, secure: true } } }
      : undefined,
    build: { outDir: 'dist', sourcemap: true },
  };
});