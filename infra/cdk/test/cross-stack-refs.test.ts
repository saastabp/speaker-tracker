import * as fs from 'fs';
import * as path from 'path';
import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';

const ENV = { account: '111111111111', region: 'us-west-2' };

const CROSS_STACK_FLAG = '@aws-cdk/core:defaultCrossStackReferences';

/**
 * `Fn::GetStackOutput` — the "weak" cross-stack reference — is emitted verbatim into the consumer
 * template and resolved by CloudFormation at deploy time. That makes the consumer template
 * **invariant to the producer's output value**, which is a silent-failure generator:
 *
 * On 2026-07-25, sandbox-Api was deleted and recreated with a new apiId. sandbox-Frontend's
 * template was byte-identical, so CloudFormation computed no diff, never updated the Distribution,
 * and the CloudFront /api/* origin went on pointing at the deleted gateway. The backend was
 * healthy, the deploy reported success, and every API call returned 502 until someone compared
 * the origin hostname against the live apiId by hand.
 *
 * `"strong"` in cdk.json restores `Fn::ImportValue` for same-region references, so CloudFormation
 * refuses to delete a producer whose exports are in use — the failure becomes loud and immediate.
 * These tests pin that, because the flag lives in a block meant to be regenerated wholesale from
 * aws-cdk-lib's recommended-feature-flags (whose recommended value is "weak").
 *
 * IMPORTANT: `new App()` does NOT read cdk.json — outside the CLI, context arrives via
 * CDK_CONTEXT_JSON, so an App built with no context sees the flag as undefined, and undefined
 * already defaults to "strong". A test that skipped loading the real context would pass no matter
 * what cdk.json said. Hence `projectContext()` below, and the positive control at the end that
 * proves the assertion discriminates.
 */
const projectContext = (): Record<string, unknown> => {
  const raw = fs.readFileSync(path.join(__dirname, '..', 'cdk.json'), 'utf8');
  return (JSON.parse(raw).context ?? {}) as Record<string, unknown>;
};

// Skip uv/vite bundling — these assertions are about references, not asset contents.
const sandboxStacks = (contextOverrides: Record<string, unknown> = {}) => {
  const app = new App({
    context: {
      ...projectContext(),
      ...contextOverrides,
      'aws:cdk:bundling-stacks': [],
    },
  });
  const api = new ApiStack(app, 'speaker-tracker-sandbox-Api', {
    env: ENV,
    appName: 'speaker-tracker',
    envType: 'sandbox',
    authMode: 'dev',
    dbName: 'speakertracker_sandbox',
    logRetention: logs.RetentionDays.ONE_MONTH,
    reservedConcurrency: {},
  });
  const frontend = new FrontendStack(app, 'speaker-tracker-sandbox-Frontend', {
    env: ENV,
    envType: 'sandbox',
    httpApi: api.httpApi,
  });
  return { api, frontend };
};

describe('cross-stack references (silent-staleness guard)', () => {
  test('cdk.json pins the flag to "strong" — do not restore the recommended "weak"', () => {
    expect(projectContext()[CROSS_STACK_FLAG]).toBe('strong');
  });

  test('the frontend consumes the API by Fn::ImportValue, never Fn::GetStackOutput', () => {
    const { frontend } = sandboxStacks();
    const body = JSON.stringify(Template.fromStack(frontend).toJSON());

    expect(body).not.toContain('Fn::GetStackOutput');
    expect(body).toContain('Fn::ImportValue');
  });

  test('the CloudFront /api origin is built from an imported apiId', () => {
    const { frontend } = sandboxStacks();
    const distributions = Template.fromStack(frontend).findResources(
      'AWS::CloudFront::Distribution',
    );
    const origins = Object.values(distributions)[0].Properties.DistributionConfig.Origins;
    const apiOrigin = origins.find((o: any) => o.CustomOriginConfig !== undefined);

    // The hostname must be assembled from a live import, not frozen into the template as a
    // literal — a literal apiId would survive a producer recreate and point at a dead gateway.
    const domain = JSON.stringify(apiOrigin.DomainName);
    expect(domain).toContain('execute-api');
    expect(domain).toContain('Fn::ImportValue');
    expect(domain).not.toContain('Fn::GetStackOutput');
  });

  test('the API stack exports the value the frontend imports', () => {
    const { api } = sandboxStacks();
    const outputs = Template.fromStack(api).findOutputs('*');

    // A strong reference only protects if the producer actually publishes an Export — that Export
    // is what makes CloudFormation refuse to delete this stack while it is in use.
    const exported = Object.values(outputs).filter((o: any) => o.Export?.Name !== undefined);
    expect(exported.length).toBeGreaterThan(0);
  });

  test('positive control: "weak" really does produce the broken Fn::GetStackOutput form', () => {
    // Proves the assertions above discriminate rather than passing vacuously. If this ever stops
    // producing Fn::GetStackOutput, the guard has quietly stopped guarding anything.
    const { frontend } = sandboxStacks({ [CROSS_STACK_FLAG]: 'weak' });
    const body = JSON.stringify(Template.fromStack(frontend).toJSON());

    expect(body).toContain('Fn::GetStackOutput');
  });
});