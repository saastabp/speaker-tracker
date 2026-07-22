#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';
import { CertStack } from '../lib/cert-stack';
import { FrontendStack } from '../lib/frontend-stack';
import {
  ACCOUNT,
  APP_NAME,
  CERT_REGION,
  COGNITO_DOMAIN_PREFIX,
  HOSTED_ZONE,
  PRIMARY_REGION,
  PROD,
  PROD_DOMAIN,
  SANDBOX,
} from '../lib/config';

/**
 * Speaker Tracker CDK app entrypoint — the composition root. Environment facts live in
 * ../lib/config; stacks are env-agnostic and receive everything via props. Stack ids are prefixed
 * with the app name so every CDK-auto-named resource is filterable by app, and every resource is
 * tagged app/environment for filtering and cost allocation.
 */
const app = new cdk.App();
const primaryEnv = { account: ACCOUNT, region: PRIMARY_REGION };

// ── Sandbox: open gateway, dev auth, default *.cloudfront.net (no Cert/Auth) ──
const sandboxApi = new ApiStack(app, `${APP_NAME}-sandbox-Api`, {
  env: primaryEnv,
  appName: APP_NAME,
  ...SANDBOX,
});

const sandboxFrontend = new FrontendStack(app, `${APP_NAME}-sandbox-Frontend`, {
  env: primaryEnv,
  envType: SANDBOX.envType,
  httpApi: sandboxApi.httpApi,
});

// ── Prod: Cognito Managed Login + us-east-1 cert + authed API ──
const prodAuth = new AuthStack(app, `${APP_NAME}-prod-Auth`, {
  env: primaryEnv,
  appUrl: `https://${PROD_DOMAIN}`,
  cognitoDomainPrefix: COGNITO_DOMAIN_PREFIX,
});

// Cert must live in us-east-1 for CloudFront; prod-Frontend (us-west-2) consumes it
// cross-region, so both ends set crossRegionReferences.
const prodCert = new CertStack(app, `${APP_NAME}-prod-Cert`, {
  env: { account: ACCOUNT, region: CERT_REGION },
  crossRegionReferences: true,
  domainName: PROD_DOMAIN,
  ...HOSTED_ZONE,
});

const prodApi = new ApiStack(app, `${APP_NAME}-prod-Api`, {
  env: primaryEnv,
  appName: APP_NAME,
  ...PROD,
  auth: { userPool: prodAuth.userPool, userPoolClient: prodAuth.userPoolClient },
});

const prodFrontend = new FrontendStack(app, `${APP_NAME}-prod-Frontend`, {
  env: primaryEnv,
  crossRegionReferences: true,
  envType: PROD.envType,
  httpApi: prodApi.httpApi,
  customDomain: { domainName: PROD_DOMAIN, certificate: prodCert.certificate, ...HOSTED_ZONE },
  auth: { userPool: prodAuth.userPool, userPoolClient: prodAuth.userPoolClient },
});

// Tags on every taggable resource: `app` separates speaker-tracker from the jobtracker/
// legacytracker siblings; `environment` splits sandbox vs prod cost. Activate `app` and
// `environment` as cost-allocation tags in the Billing console to see them in Cost Explorer.
cdk.Tags.of(app).add('app', APP_NAME);
cdk.Tags.of(app).add('managed-by', 'cdk');
for (const stack of [sandboxApi, sandboxFrontend]) {
  cdk.Tags.of(stack).add('environment', 'sandbox');
}
for (const stack of [prodAuth, prodCert, prodApi, prodFrontend]) {
  cdk.Tags.of(stack).add('environment', 'prod');
}

app.synth();