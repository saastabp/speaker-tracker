import * as logs from 'aws-cdk-lib/aws-logs';

/** App name — prefixes stack ids and Lambda/log-group names, and tags every resource, so
 *  speaker-tracker resources are filterable apart from the jobtracker/legacytracker siblings. */
export const APP_NAME = 'speaker-tracker';

export const ACCOUNT = '381492047863';
export const PRIMARY_REGION = 'us-west-2';
export const CERT_REGION = 'us-east-1'; // CloudFront requires the cert in us-east-1

/** Route53 hosted zone for the parent domain (same account). */
export const HOSTED_ZONE = {
  hostedZoneId: 'Z08490251WV9146J97IRG',
  zoneName: '360balancedliving.com',
} as const;

/** Prod public hostname + Cognito hosted-domain prefix. */
export const PROD_DOMAIN = 'speaker-tracker.360balancedliving.com';
export const COGNITO_DOMAIN_PREFIX = 'speakertracker-app-381492047863';

/* ── Email (slice 6a) ────────────────────────────────────────────────────────────────────────
 * Plain constants, not cross-stack references: the Messaging stack creates the IMAP secret and
 * nothing imports from it, so the Api and Messaging stacks stay independently deployable. A
 * stale cross-stack value is what silently broke the sandbox CloudFront origin on 2026-07-25.
 */

/** Domain identity verified in THIS account (Verified, DKIM SUCCESS, signing enabled). The same
 *  domain is separately verified in Donna's WorkMail account (730335513412); that identity is
 *  deliberately NOT used — it would need cross-account sending authorization, and SES production
 *  access was granted here. CDK **references** this ARN and must never create the identity: it is
 *  shared with other senders on the domain, and a CDK-owned identity could delete their
 *  verification. */
export const SES_IDENTITY_ARN =
  'arn:aws:ses:us-east-1:381492047863:identity/360balancedliving.com';

/** SES endpoint region — where the identity and the WorkMail mailbox live, not where the app runs. */
export const SES_REGION = 'us-east-1';

/** WorkMail IMAP endpoint. Username/password, not IAM, so reaching Donna's mailbox from this
 *  account is not a cross-account problem at all. */
export const IMAP_HOST = 'imap.mail.us-east-1.awsapps.com';

/** Secrets Manager name for the IMAP credentials, **scoped per environment**. CDK creates the
 *  secret **empty**; the value is written once by hand, so the password never enters cdk.out, the
 *  staging bucket, or the CloudFormation template.
 *
 *  Env-scoped rather than one shared `speakertracker/imap`: a single name would collide the moment
 *  both `<env>-Messaging` stacks exist (the second create fails), and sharing one secret between
 *  environments means rotating prod silently changes sandbox. The cost is writing the same value
 *  twice — a 30-second manual step that happens twice ever. It also leaves room for a separate
 *  test mailbox later without restructuring. */
export const imapSecretName = (envType: 'sandbox' | 'prod'): string =>
  `speakertracker/${envType}/imap`;

/** Envelope sender. The real mailbox address, not a bare `donna@` — this is where replies land,
 *  and 6b's poller reads that mailbox. */
export const MAIL_FROM_ADDRESS = 'donna.king@360balancedliving.com';
export const MAIL_FROM_NAME = 'Donna King';

/** Per-environment knobs shared by the Api/Frontend stacks. */
export interface EnvConfig {
  readonly envType: 'sandbox' | 'prod';
  readonly authMode: 'dev' | 'cognito';
  readonly dbName: string;
  readonly logRetention: logs.RetentionDays;
  /** Reserved concurrency per function. Empty = none — required for sandbox until the account's
   *  Lambda concurrency quota is raised above 10 (reserving anything drops unreserved below the
   *  hard floor of 10). Prod reserves to bound connections on the shared db.t4g.micro. */
  readonly reservedConcurrency: { readonly api?: number; readonly migrate?: number };
}

export const SANDBOX: EnvConfig = {
  envType: 'sandbox',
  authMode: 'dev',
  dbName: 'speakertracker_sandbox',
  logRetention: logs.RetentionDays.ONE_MONTH,
  reservedConcurrency: {}, // none — account concurrency limit is 10 (quota increase pending)
};

export const PROD: EnvConfig = {
  envType: 'prod',
  authMode: 'cognito',
  dbName: 'speakertracker',
  logRetention: logs.RetentionDays.THREE_MONTHS,
  reservedConcurrency: { api: 5, migrate: 1 },
};