#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';
import { CertStack } from '../lib/cert-stack';
import { FrontendStack } from '../lib/frontend-stack';
import {
  ACCOUNT,
  CERT_REGION,
  COGNITO_DOMAIN_PREFIX,
  HOSTED_ZONE,
  PRIMARY_REGION,
  PROD,
  PROD_DOMAIN,
  SANDBOX,
} from '../lib/config';

/**
 * Speaker Tracker CDK app entrypoint — the composition root. Environment facts
 * (accounts, regions, domains, per-env knobs) live in ../lib/config; stacks are
 * env-agnostic and receive everything via props.
 */
const app = new cdk.App();
const primaryEnv = { account: ACCOUNT, region: PRIMARY_REGION };

// ── Sandbox: open gateway, dev auth, default *.cloudfront.net (no Cert/Auth) ──
const sandboxApi = new ApiStack(app, 'sandbox-Api', { env: primaryEnv, ...SANDBOX });

new FrontendStack(app, 'sandbox-Frontend', {
  env: primaryEnv,
  envType: SANDBOX.envType,
  httpApi: sandboxApi.httpApi,
});

// ── Prod: Cognito Managed Login + us-east-1 cert + authed API ──
const prodAuth = new AuthStack(app, 'prod-Auth', {
  env: primaryEnv,
  appUrl: `https://${PROD_DOMAIN}`,
  cognitoDomainPrefix: COGNITO_DOMAIN_PREFIX,
});

// Cert must live in us-east-1 for CloudFront; prod-Frontend (us-west-2) consumes it
// cross-region, so both ends set crossRegionReferences.
const prodCert = new CertStack(app, 'prod-Cert', {
  env: { account: ACCOUNT, region: CERT_REGION },
  crossRegionReferences: true,
  domainName: PROD_DOMAIN,
  ...HOSTED_ZONE,
});

const prodApi = new ApiStack(app, 'prod-Api', {
  env: primaryEnv,
  ...PROD,
  auth: { userPool: prodAuth.userPool, userPoolClient: prodAuth.userPoolClient },
});

new FrontendStack(app, 'prod-Frontend', {
  env: primaryEnv,
  crossRegionReferences: true,
  envType: PROD.envType,
  httpApi: prodApi.httpApi,
  customDomain: { domainName: PROD_DOMAIN, certificate: prodCert.certificate, ...HOSTED_ZONE },
});

app.synth();