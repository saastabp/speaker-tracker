#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import * as logs from 'aws-cdk-lib/aws-logs';
import { ApiStack } from '../lib/api-stack';
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

app.synth();