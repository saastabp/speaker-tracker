/**
 * The SPA artifact must differ between environments (regression, 2026-08-03).
 *
 * Production's first deploy served the *sandbox* bundle. The source tree is byte-identical for both
 * — `mode` only chooses which npm script runs — and CDK's default hash for a bundled asset is
 * `sha256(fingerprint(source) + JSON.stringify(bundling))`, where `bundling.local` is a function
 * that `JSON.stringify` drops. Both modes therefore hashed the same, CDK bundled once, and both
 * Frontend stacks pointed at one artifact. `bin/app.ts` builds sandbox first, so production got
 * `VITE_AUTH_MODE=dev`: Cognito never contacted, every API call answered `unauthorized`.
 *
 * Nothing failed. Synth succeeded, every stack deployed green, and the only visible symptom was the
 * app itself. This file is the check that would have caught it.
 */
import { App, Stack } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { TEST_EMAIL_CONFIG, TEST_POLL_CONFIG } from './fixtures';

const ENV = { account: '111111111111', region: 'us-west-2' };

/** The object keys a stack's BucketDeployment uploads — the SPA artifact plus CDK's own helper. */
function sourceObjectKeys(stack: Stack): string[] {
  const deployments = Template.fromStack(stack).findResources('Custom::CDKBucketDeployment');
  return Object.values(deployments).flatMap((d) =>
    JSON.stringify(d.Properties.SourceObjectKeys).match(/[0-9a-f]{64}/g) ?? [],
  );
}

function frontends() {
  // Bundling is skipped — these assertions are about which artifact each stack references, not its
  // contents, and the custom asset hash still varies by mode on the skip path.
  const app = new App({ context: { 'aws:cdk:bundling-stacks': [] } });
  const api = new ApiStack(app, 'TestApi', {
    env: ENV,
    appName: 'speaker-tracker',
    envType: 'sandbox',
    authMode: 'dev',
    dbName: 'speakertracker_sandbox',
    logRetention: logs.RetentionDays.ONE_MONTH,
    reservedConcurrency: {},
    ...TEST_POLL_CONFIG,
    email: TEST_EMAIL_CONFIG,
    contentCorsOrigins: ['https://example.test'],
  });
  return {
    sandbox: new FrontendStack(app, 'TestSandboxFrontend', {
      env: ENV,
      envType: 'sandbox',
      httpApi: api.httpApi,
    }),
    prod: new FrontendStack(app, 'TestProdFrontend', {
      env: ENV,
      envType: 'prod',
      httpApi: api.httpApi,
    }),
  };
}

describe('the SPA artifact is per-environment', () => {
  test('sandbox and prod do not deploy the same object', () => {
    const { sandbox, prod } = frontends();

    const sandboxKeys = sourceObjectKeys(sandbox);
    const prodKeys = sourceObjectKeys(prod);

    expect(sandboxKeys.length).toBeGreaterThan(0);
    // At least one key unique to each side. A plain inequality would also pass if the two merely
    // uploaded different *numbers* of shared objects, which is not what is being asserted.
    expect(sandboxKeys.some((key) => !prodKeys.includes(key))).toBe(true);
    expect(prodKeys.some((key) => !sandboxKeys.includes(key))).toBe(true);
  });

  test('the difference survives building the two in either order', () => {
    // The original bug was order-dependent — whichever stack synthesized first won, and the other
    // silently reused its artifact. Constructing prod first must give the same answer.
    const app = new App({ context: { 'aws:cdk:bundling-stacks': [] } });
    const api = new ApiStack(app, 'TestApi', {
      env: ENV,
      appName: 'speaker-tracker',
      envType: 'sandbox',
      authMode: 'dev',
      dbName: 'speakertracker_sandbox',
      logRetention: logs.RetentionDays.ONE_MONTH,
      reservedConcurrency: {},
      ...TEST_POLL_CONFIG,
      email: TEST_EMAIL_CONFIG,
      contentCorsOrigins: ['https://example.test'],
    });
    const prod = new FrontendStack(app, 'TestProdFirst', {
      env: ENV,
      envType: 'prod',
      httpApi: api.httpApi,
    });
    const sandbox = new FrontendStack(app, 'TestSandboxSecond', {
      env: ENV,
      envType: 'sandbox',
      httpApi: api.httpApi,
    });

    expect(sourceObjectKeys(prod)).not.toEqual(sourceObjectKeys(sandbox));
  });
});