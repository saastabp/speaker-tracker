#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';
import { CertStack } from '../lib/cert-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { MessagingStack } from '../lib/messaging-stack';
import {
  ACCOUNT,
  APP_NAME,
  CERT_REGION,
  COGNITO_DOMAIN_PREFIX,
  contentCorsOrigins,
  HOSTED_ZONE,
  IMAP_HOST,
  imapSecretName,
  MAIL_FROM_ADDRESS,
  MAIL_FROM_NAME,
  PRIMARY_REGION,
  PROD,
  PROD_DOMAIN,
  SANDBOX,
  SES_IDENTITY_ARN,
} from '../lib/config';

/**
 * Speaker Tracker CDK app entrypoint — the composition root. Environment facts live in
 * ../lib/config; stacks are env-agnostic and receive everything via props. Stack ids are prefixed
 * with the app name so every CDK-auto-named resource is filterable by app, and every resource is
 * tagged app/environment for filtering and cost allocation.
 */
const app = new cdk.App();
const primaryEnv = { account: ACCOUNT, region: PRIMARY_REGION };

/** Email wiring. The mailbox and sending identity are the same in both environments; only the
 *  secret *name* is env-scoped, so the two Messaging stacks cannot collide on it. */
const emailFor = (envType: 'sandbox' | 'prod') =>
  ({
    sesIdentityArn: SES_IDENTITY_ARN,
    imapHost: IMAP_HOST,
    imapSecretName: imapSecretName(envType),
    mailFromAddress: MAIL_FROM_ADDRESS,
    mailFromName: MAIL_FROM_NAME,
  }) as const;

// ── Sandbox: open gateway, dev auth, default *.cloudfront.net (no Cert/Auth) ──
const sandboxMessaging = new MessagingStack(app, `${APP_NAME}-sandbox-Messaging`, {
  env: primaryEnv,
  envType: SANDBOX.envType,
  imapSecretName: imapSecretName(SANDBOX.envType),
  sesIdentityArn: SES_IDENTITY_ARN,
  imapHost: IMAP_HOST,
});

const sandboxApi = new ApiStack(app, `${APP_NAME}-sandbox-Api`, {
  env: primaryEnv,
  appName: APP_NAME,
  email: emailFor(SANDBOX.envType),
  contentCorsOrigins: contentCorsOrigins(SANDBOX.envType),
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

const prodMessaging = new MessagingStack(app, `${APP_NAME}-prod-Messaging`, {
  env: primaryEnv,
  envType: PROD.envType,
  imapSecretName: imapSecretName(PROD.envType),
  sesIdentityArn: SES_IDENTITY_ARN,
  imapHost: IMAP_HOST,
});

const prodApi = new ApiStack(app, `${APP_NAME}-prod-Api`, {
  env: primaryEnv,
  appName: APP_NAME,
  email: emailFor(PROD.envType),
  contentCorsOrigins: contentCorsOrigins(PROD.envType),
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
for (const stack of [sandboxMessaging, sandboxApi, sandboxFrontend]) {
  cdk.Tags.of(stack).add('environment', 'sandbox');
}
for (const stack of [prodAuth, prodCert, prodMessaging, prodApi, prodFrontend]) {
  cdk.Tags.of(stack).add('environment', 'prod');
}

app.synth();