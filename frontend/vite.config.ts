import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

/**
 * The commit this bundle was built from, plus `-dirty` when the tree had uncommitted changes.
 *
 * Baked in because the browser has no other way to say which deploy it is running, and "which
 * build were you on?" is the first question any bug report needs answered. The `-dirty` marker
 * matters more than the sha: it says a deploy came from code that was never committed, which is
 * exactly the case where what is live cannot be reproduced.
 *
 * Falls back to 'unknown' rather than throwing — a missing ref is a bad reason to fail a build,
 * and CDK synth runs this too.
 */
function buildRef(): string {
  const git = (args: string) =>
    execSync(`git ${args}`, { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim();
  try {
    const sha = git('rev-parse --short HEAD');
    return git('status --porcelain') ? `${sha}-dirty` : sha;
  } catch {
    return 'unknown';
  }
}

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
    // No source maps in production (they would expose the original TS to anyone opening devtools
    // once the dist is served). Sandbox keeps them for debugging the deployed bundle; local
    // `vite dev` has its own maps regardless.
    build: { outDir: 'dist', sourcemap: mode === 'production' ? false : true },
    define: {
      __APP_VERSION__: JSON.stringify(pkg.version),
      __BUILD_REF__: JSON.stringify(buildRef()),
    },
  };
});