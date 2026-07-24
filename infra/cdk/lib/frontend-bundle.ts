import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { DockerImage } from 'aws-cdk-lib';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';

/** frontend/ project root, relative to this file (infra/cdk/lib). */
const FRONTEND_DIR = path.resolve(__dirname, '..', '..', '..', 'frontend');

/**
 * Build the Vite SPA for the given env and return it as an s3-deployment Source, natively (no
 * Docker). Mirrors ``backendBundle`` in ``python-lambda.ts``: the deployable artifact is built at
 * synth time, so ``cdk synth``/``deploy`` needs no separate frontend build step and can never ship
 * a stale ``dist``. CDK re-bundles only when the frontend source fingerprint changes.
 *
 * ``mode`` selects the Vite build: ``sandbox`` omits source maps' prod stripping and keeps the dev
 * OIDC fallback; ``production`` is the hardened build. Each ``FrontendStack`` passes its own env.
 */
export function frontendBundle(mode: 'sandbox' | 'production'): s3deploy.ISource {
  return s3deploy.Source.asset(FRONTEND_DIR, {
    // Keep build output and deps out of the source fingerprint (the deployed content comes from
    // buildFrontend's copy; hashing node_modules/dist would churn the asset and bloat synth).
    exclude: ['node_modules', 'dist', '.vite'],
    bundling: {
      // Docker fallback is unreachable — buildFrontend throws on failure rather than returning
      // false — but BundlingOptions requires an image.
      image: DockerImage.fromRegistry('node:20'),
      local: {
        tryBundle(outputDir: string): boolean {
          buildFrontend(mode, outputDir);
          return true;
        },
      },
    },
  });
}

function buildFrontend(mode: 'sandbox' | 'production', outputDir: string): void {
  // Route child stdout to stderr (fd 2) so npm/vite output never lands on cdk's stdout.
  const run = (file: string, args: string[]) =>
    execFileSync(file, args, { cwd: FRONTEND_DIR, stdio: ['ignore', 2, 2] });

  // Self-contained: install deps if absent (a fresh CI runner or clean checkout), so synth never
  // depends on a prior frontend build step.
  if (!fs.existsSync(path.join(FRONTEND_DIR, 'node_modules'))) {
    run('npm', ['ci']);
  }

  run('npm', ['run', mode === 'production' ? 'build:production' : 'build:sandbox']);

  // Vite always writes to frontend/dist; copy that into the asset staging dir CDK will deploy.
  fs.cpSync(path.join(FRONTEND_DIR, 'dist'), outputDir, { recursive: true });
}