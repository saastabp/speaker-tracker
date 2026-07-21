import { execFileSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as lambda from 'aws-cdk-lib/aws-lambda';

/** Runtime + architecture for every function in this app (matches the uv bundle target). */
export const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_12;
export const LAMBDA_ARCH = lambda.Architecture.ARM_64;

/** manylinux wheel tag uv installs for — must correspond to LAMBDA_ARCH. */
const UV_PLATFORM = 'aarch64-manylinux2014';

/** backend/ project root, relative to this file (infra/cdk/lib). */
const BACKEND_DIR = path.resolve(__dirname, '..', '..', '..', 'backend');

/**
 * Build the shared Python bundle with uv, natively (no Docker), and return it as
 * Lambda Code. boto3/botocore are dropped — the Lambda runtime provides them (see
 * the bundle-packaging notes). CDK caches the result within a synth, so calling
 * this for several functions bundles only once.
 */
export function backendBundle(): lambda.Code {
  return lambda.Code.fromAsset(BACKEND_DIR, {
    bundling: {
      // Docker fallback is unreachable — buildBundle throws on failure rather than
      // returning false — but BundlingOptions requires an image.
      image: PYTHON_RUNTIME.bundlingImage,
      local: {
        tryBundle(outputDir: string): boolean {
          buildBundle(outputDir);
          return true;
        },
      },
    },
  });
}

function buildBundle(outputDir: string): void {
  // Route child stdout to stderr (fd 2) so uv output never lands on cdk's stdout.
  const run = (file: string, args: string[]) =>
    execFileSync(file, args, { cwd: BACKEND_DIR, stdio: ['ignore', 2, 2] });

  const requirements = path.join(outputDir, 'requirements.txt');

  // 1. Resolve deps from the lockfile, minus the runtime-provided SDK.
  run('uv', [
    'export', '--frozen', '--no-dev',
    '--no-emit-package', 'boto3',
    '--no-emit-package', 'botocore',
    '-o', requirements,
  ]);

  // 2. Install arm64 wheels into the bundle. --no-deps installs exactly the
  //    lockfile's flattened closure (which excludes boto3/botocore — the runtime
  //    provides them); without it, uv re-resolves and pulls botocore back via
  //    aws-xray-sdk. --only-binary is load-bearing: without it uv would build an
  //    sdist for the host (x86) and ship a broken .so.
  run('uv', [
    'pip', 'install',
    '-r', requirements,
    '--target', outputDir,
    '--python-platform', UV_PLATFORM,
    '--only-binary', ':all:',
    '--no-deps',
  ]);

  // 3. Drop the temp requirements, then copy application source to the bundle root
  //    (api_handler, app, common, core, handlers, models, repositories, migrations).
  fs.rmSync(requirements, { force: true });
  fs.cpSync(path.join(BACKEND_DIR, 'src'), outputDir, { recursive: true });
}