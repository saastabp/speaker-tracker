import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { ApiStack } from '../lib/api-stack';
import { TEST_EMAIL_CONFIG, TEST_POLL_CONFIG } from './fixtures';

const ENV = { account: '111111111111', region: 'us-west-2' };

// Skip uv bundling — these assertions are about the resource policy, not Lambda code.
const newApp = () => new App({ context: { 'aws:cdk:bundling-stacks': [] } });

/**
 * AWS caps a Lambda function's **resource policy** at 20,480 bytes. Each
 * AWS::Lambda::Permission the stack emits becomes one statement in that policy, so a design
 * that emits one permission per API route grows the policy linearly with the route table and
 * eventually fails the deploy — not at synth, not at review, but partway through CloudFormation,
 * with routes already half-created:
 *
 *   The final policy size (20662) is bigger than the limit (20480)
 *
 * That is exactly what happened on 2026-07-25 at 58 routes. The fix was
 * `scopePermissionToRoute: false` on the shared HttpLambdaIntegration, which collapses every
 * route's grant into a single api-scoped permission. These tests keep it that way: the first
 * pins the mechanism, the second is the backstop that fires if some future integration starts
 * emitting per-route grants again.
 */
const POLICY_LIMIT_BYTES = 20_480;

/** Headroom below the hard limit — fail while there is still room to fix it, not at the wall. */
const POLICY_BUDGET_BYTES = 15_000;

const apiStack = () =>
  new ApiStack(newApp(), 'sandbox-Api', {
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

/**
 * Approximate the deployed resource-policy size from the synthesized permissions.
 *
 * CloudFormation resolves each permission into an IAM statement whose serialized form is close
 * to the JSON of its properties plus the statement scaffolding AWS adds (Sid, Effect, unresolved
 * Ref/Fn::Join collapsing to real ARNs). This over-counts slightly on the intrinsics and
 * under-counts on the scaffolding, which is fine: it is a trend alarm, not an exact byte count.
 */
const estimatedPolicyBytes = (t: Template): number =>
  Object.values(t.findResources('AWS::Lambda::Permission'))
    .map((r) => JSON.stringify(r.Properties).length + 120)
    .reduce((total, size) => total + size, 0);

describe('Lambda resource-policy size (deploy-blocking AWS limit)', () => {
  test('routes share one api-scoped permission instead of one per route', () => {
    const template = Template.fromStack(apiStack());

    const routes = Object.keys(template.findResources('AWS::ApiGatewayV2::Route')).length;
    const permissions = template.findResources('AWS::Lambda::Permission');
    const apiPermissions = Object.values(permissions).filter(
      (r) => r.Properties?.Principal === 'apigateway.amazonaws.com',
    );

    // The regression this guards: permissions must NOT scale with the route table.
    expect(routes).toBeGreaterThan(10);
    expect(apiPermissions).toHaveLength(1);

    // And the single grant must be api-scoped (`<apiId>/*/*/*`), not pinned to one route path.
    const sourceArn = JSON.stringify(apiPermissions[0].Properties.SourceArn);
    expect(sourceArn).toContain('/*/*/*');
  });

  test('estimated resource policy stays well under the 20,480-byte cap', () => {
    const bytes = estimatedPolicyBytes(Template.fromStack(apiStack()));

    expect(bytes).toBeLessThan(POLICY_BUDGET_BYTES);
    expect(bytes).toBeLessThan(POLICY_LIMIT_BYTES);
  });
});