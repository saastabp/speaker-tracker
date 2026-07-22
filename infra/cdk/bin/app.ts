#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import * as logs from 'aws-cdk-lib/aws-logs';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';
import { CertStack } from '../lib/cert-stack';
import { FrontendStack } from '../lib/frontend-stack';

/**
 * Speaker Tracker CDK app entrypoint. Stacks are added in this order (see
 * ARCHITECTURE.md §6 and the cdk-setup notes):
 *   1. sandbox Api + Frontend   (open gateway, AUTH_MODE=dev)  ← in progress
 *   2. prod Auth + Cert         (Cognito Managed Login; ACM in us-east-1)
 *   3. prod Api + Frontend      (conditional JWT authorizer)
 */

/** Account + primary region for every stack (cert is us-east-1, added later). */
const ACCOUNT = '381492047863';
const PRIMARY_REGION = 'us-west-2';

/** Prod public hostname + Cognito hosted-domain prefix. */
const PROD_DOMAIN = 'speaker-tracker.360balancedliving.com';
const COGNITO_DOMAIN_PREFIX = 'speakertracker-app-381492047863';

/** Route53 hosted zone for the parent domain (same account, us-east-1 cert validation). */
const HOSTED_ZONE_ID = 'Z08490251WV9146J97IRG';
const ZONE_NAME = '360balancedliving.com';
const US_EAST_1 = 'us-east-1';

const app = new cdk.App();

// ── Sandbox: open gateway, dev auth, default *.cloudfront.net (no Cert/Auth) ──
const sandboxEnv = { account: ACCOUNT, region: PRIMARY_REGION };

const sandboxApi = new ApiStack(app, 'sandbox-Api', {
  env: sandboxEnv,
  envType: 'sandbox',
  authMode: 'dev',
  dbName: 'speakertracker_sandbox',
  logRetention: logs.RetentionDays.ONE_MONTH,
});

new FrontendStack(app, 'sandbox-Frontend', {
  env: sandboxEnv,
  envType: 'sandbox',
  httpApi: sandboxApi.httpApi,
});

// ── Prod: Cognito Managed Login auth (Cert + authed Api/Frontend added next) ──
const prodEnv = { account: ACCOUNT, region: PRIMARY_REGION };

const prodAuth = new AuthStack(app, 'prod-Auth', {
  env: prodEnv,
  appUrl: `https://${PROD_DOMAIN}`,
  cognitoDomainPrefix: COGNITO_DOMAIN_PREFIX,
});

// Cert must live in us-east-1 for CloudFront; prod-Frontend (us-west-2) consumes it
// cross-region, so both ends set crossRegionReferences.
const prodCert = new CertStack(app, 'prod-Cert', {
  env: { account: ACCOUNT, region: US_EAST_1 },
  crossRegionReferences: true,
  domainName: PROD_DOMAIN,
  hostedZoneId: HOSTED_ZONE_ID,
  zoneName: ZONE_NAME,
});

const prodApi = new ApiStack(app, 'prod-Api', {
  env: prodEnv,
  envType: 'prod',
  authMode: 'cognito',
  dbName: 'speakertracker',
  logRetention: logs.RetentionDays.THREE_MONTHS,
  auth: { userPool: prodAuth.userPool, userPoolClient: prodAuth.userPoolClient },
});

new FrontendStack(app, 'prod-Frontend', {
  env: prodEnv,
  crossRegionReferences: true,
  envType: 'prod',
  httpApi: prodApi.httpApi,
  customDomain: {
    domainName: PROD_DOMAIN,
    certificate: prodCert.certificate,
    hostedZoneId: HOSTED_ZONE_ID,
    zoneName: ZONE_NAME,
  },
});

app.synth();