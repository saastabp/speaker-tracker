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