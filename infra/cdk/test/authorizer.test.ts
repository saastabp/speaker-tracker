import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';

const ENV = { account: '111111111111', region: 'us-west-2' };

// Skip uv bundling — these assertions are about routes/authorizers, not Lambda code.
const newApp = () => new App({ context: { 'aws:cdk:bundling-stacks': [] } });

const routeProps = (t: Template): Record<string, any>[] =>
  Object.values(t.findResources('AWS::ApiGatewayV2::Route')).map((r) => r.Properties);

describe('API Gateway authorizer wiring (security invariant, not runtime enforcement)', () => {
  test('sandbox locks no route — open gateway, zero authorizers', () => {
    const stack = new ApiStack(newApp(), 'sandbox-Api', {
      env: ENV,
      envType: 'sandbox',
      authMode: 'dev',
      dbName: 'speakertracker_sandbox',
      logRetention: logs.RetentionDays.ONE_MONTH,
    });
    const template = Template.fromStack(stack);

    const routes = routeProps(template);
    expect(routes.length).toBeGreaterThan(0);
    for (const p of routes) {
      expect(p.AuthorizerId).toBeUndefined();
      expect(p.AuthorizationType).toBe('NONE');
    }
    template.resourceCountIs('AWS::ApiGatewayV2::Authorizer', 0);
  });

  test('prod locks every route except GET /health with the JWT authorizer', () => {
    const app = newApp();
    const auth = new AuthStack(app, 'prod-Auth', {
      env: ENV,
      appUrl: 'https://speaker-tracker.example.com',
      cognitoDomainPrefix: 'speakertracker-test',
    });
    const stack = new ApiStack(app, 'prod-Api', {
      env: ENV,
      envType: 'prod',
      authMode: 'cognito',
      dbName: 'speakertracker',
      logRetention: logs.RetentionDays.THREE_MONTHS,
      auth: { userPool: auth.userPool, userPoolClient: auth.userPoolClient },
    });
    const template = Template.fromStack(stack);

    const byKey: Record<string, any> = {};
    for (const p of routeProps(template)) byKey[p.RouteKey] = p;

    // /health stays open for uptime checks.
    expect(byKey['GET /health']).toBeDefined();
    expect(byKey['GET /health'].AuthorizerId).toBeUndefined();
    expect(byKey['GET /health'].AuthorizationType).toBe('NONE');

    // Every other route is protected by the JWT authorizer.
    const others = Object.entries(byKey).filter(([k]) => k !== 'GET /health');
    expect(others.length).toBeGreaterThan(0);
    for (const [, p] of others) {
      expect(p.AuthorizationType).toBe('JWT');
      expect(p.AuthorizerId).toBeDefined();
    }
    template.resourceCountIs('AWS::ApiGatewayV2::Authorizer', 1);
  });
});