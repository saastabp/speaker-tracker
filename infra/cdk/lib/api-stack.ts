import { ArnFormat, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpJwtAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as triggers from 'aws-cdk-lib/triggers';
import { Construct } from 'constructs';
import { SharedDatabase } from './shared-db';
import { PYTHON_RUNTIME, LAMBDA_ARCH, backendBundle } from './python-lambda';

/** IAM DB user speaker-tracker connects as (same in both envs; schema differs). */
const DB_USER = 'speakertracker_app';

interface RouteDef {
  readonly method: apigwv2.HttpMethod;
  readonly path: string;
  /** Whether this route carries the JWT authorizer when auth is configured (prod). */
  readonly authRequired: boolean;
}

/** Route → authorizer table for the HTTP API. `/health` stays open for uptime checks; every other
 *  route carries the JWT authorizer in prod (open in sandbox). Routes are declared explicitly (not
 *  ANY /{proxy+}), so **a new backend Router module must have its paths added here too**. */
const ROUTES: RouteDef[] = [
  { method: apigwv2.HttpMethod.GET, path: '/health', authRequired: false },
  { method: apigwv2.HttpMethod.GET, path: '/catalogs', authRequired: true },
  // Organizations (slice 2)
  { method: apigwv2.HttpMethod.GET, path: '/organizations', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/organizations', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/organizations/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/organizations/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/organizations/{id}', authRequired: true },
  // Contacts (slice 2)
  { method: apigwv2.HttpMethod.GET, path: '/contacts', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/contacts', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/contacts/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/contacts/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/contacts/{id}', authRequired: true },
  // Contact-organization affiliations (slice 2)
  { method: apigwv2.HttpMethod.POST, path: '/contacts/{id}/organizations', authRequired: true },
  {
    method: apigwv2.HttpMethod.PUT,
    path: '/contacts/{id}/organizations/{orgId}',
    authRequired: true,
  },
  {
    method: apigwv2.HttpMethod.DELETE,
    path: '/contacts/{id}/organizations/{orgId}',
    authRequired: true,
  },

  { method: apigwv2.HttpMethod.GET, path: '/talks', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/talks', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/talks/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/talks/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/talks/{id}', authRequired: true },

  { method: apigwv2.HttpMethod.GET, path: '/funnel', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/opportunities', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/opportunities', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/opportunities/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/opportunities/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/opportunities/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.PATCH, path: '/opportunities/{id}/status', authRequired: true },
  { method: apigwv2.HttpMethod.PATCH, path: '/opportunities/{id}/payment', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/opportunities/{id}/close', authRequired: true },

  { method: apigwv2.HttpMethod.POST, path: '/opportunities/{id}/contacts', authRequired: true },
  {
    method: apigwv2.HttpMethod.PUT,
    path: '/opportunities/{id}/contacts/{contactId}',
    authRequired: true,
  },
  {
    method: apigwv2.HttpMethod.DELETE,
    path: '/opportunities/{id}/contacts/{contactId}',
    authRequired: true,
  },

  { method: apigwv2.HttpMethod.POST, path: '/opportunities/{id}/notes', authRequired: true },
  {
    method: apigwv2.HttpMethod.DELETE,
    path: '/opportunities/{id}/notes/{noteId}',
    authRequired: true,
  },

  // Slice 4 — outreach journal + contact timeline (handlers/outreaches.py).
  { method: apigwv2.HttpMethod.POST, path: '/outreaches', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/outreaches/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/contacts/{id}/outreaches', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/contacts/{id}/timeline', authRequired: true },

  // Slice 4 — message templates (handlers/message_templates.py).
  { method: apigwv2.HttpMethod.GET, path: '/templates', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/templates', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/templates/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/templates/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/templates/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/templates/{id}/duplicate', authRequired: true },

  // Slice 5 — targets + dashboard (handlers/targets.py, handlers/dashboard.py).
  { method: apigwv2.HttpMethod.GET, path: '/targets', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/targets', authRequired: true },
  {
    method: apigwv2.HttpMethod.DELETE,
    path: '/targets/{targetType}/{cadence}',
    authRequired: true,
  },
  { method: apigwv2.HttpMethod.GET, path: '/dashboard', authRequired: true },

  // Slice 6a — email send path and thread reads (handlers/emails.py). Everything under /emails
  // so the whole feature is one block here and one router module there.
  { method: apigwv2.HttpMethod.POST, path: '/emails/send', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/emails/attachments', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/emails/threads', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/emails/threads/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/emails/threads/{id}/replies', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/emails/threads/{id}/read', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/emails/threads/{id}/close', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/emails/threads/{id}/reopen', authRequired: true },

  // Slice 6b — the pending-import queue (handlers/email_imports.py). PUT rather than POST on both
  // links: they set a property and re-sending the same value succeeds, unlike the close/reopen
  // verbs above, whose second application is a 404.
  { method: apigwv2.HttpMethod.GET, path: '/emails/imports', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/emails/threads/{id}/contact', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/emails/threads/{id}/opportunity', authRequired: true },

  // Slice 6a — email signatures (handlers/signatures.py).
  { method: apigwv2.HttpMethod.GET, path: '/signatures', authRequired: true },
  { method: apigwv2.HttpMethod.GET, path: '/signatures/default', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/signatures', authRequired: true },
  { method: apigwv2.HttpMethod.PUT, path: '/signatures/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/signatures/{id}', authRequired: true },

  // Slice 7 — follow-up reminders (handlers/follow_ups.py). Flat, with the contact/opportunity
  // links as query filters rather than nested paths, so one route serves the Follow-ups page and
  // both detail panels. Marking done is PATCH with `completed`, not a route of its own.
  { method: apigwv2.HttpMethod.GET, path: '/follow-ups', authRequired: true },
  { method: apigwv2.HttpMethod.POST, path: '/follow-ups', authRequired: true },
  { method: apigwv2.HttpMethod.PATCH, path: '/follow-ups/{id}', authRequired: true },
  { method: apigwv2.HttpMethod.DELETE, path: '/follow-ups/{id}', authRequired: true },
];

export interface ApiStackProps extends StackProps {
  /** App name prefix for resource naming (e.g. `speaker-tracker`). */
  readonly appName: string;
  readonly envType: 'sandbox' | 'prod';
  readonly authMode: 'dev' | 'cognito';
  /** Schema selected on connect: `speakertracker` | `speakertracker_sandbox`. */
  readonly dbName: string;
  readonly logRetention: logs.RetentionDays;
  /** Reserved concurrency per function; omit a value → no reservation. */
  readonly reservedConcurrency: {
    readonly api?: number;
    readonly migrate?: number;
    readonly poll?: number;
  };
  /** Whether this environment's IMAP poller runs on its schedule. Exactly one environment may
   *  poll a given mailbox — see `config.ts`. The function is deployed either way, so a disabled
   *  environment can still be invoked by hand for testing. */
  readonly pollEnabled: boolean;
  /** Address the poller's failure alarm notifies. */
  readonly alarmEmail: string;
  /** Cognito wiring for the JWT authorizer. Absent → open gateway (sandbox). */
  readonly auth?: {
    readonly userPool: cognito.IUserPool;
    readonly userPoolClient: cognito.IUserPoolClient;
  };
  /** Origins allowed to PUT composer attachments straight to the content bucket. Supplied by
   *  `bin/app.ts` so this stack needs no reference to the Frontend's CloudFront domain. */
  readonly contentCorsOrigins: string[];
  /** Email wiring (slice 6a). Passed as props, not imported from config, so the stack stays
   *  env-agnostic and testable — only `bin/app.ts` reads `config.ts`. */
  readonly email: {
    /** SES domain identity to send as. **Referenced, never created** — it is shared with other
     *  senders on the domain, so a CDK-owned identity could delete their verification. */
    readonly sesIdentityArn: string;
    readonly imapHost: string;
    /** Secrets Manager name; the secret itself is created (empty) by the Messaging stack. */
    readonly imapSecretName: string;
    readonly mailFromAddress: string;
    readonly mailFromName: string;
  };
}

/**
 * The API stack: one Lambda serving every route behind an HTTP API, a separate
 * migrate function run in-deploy, and a conditional Cognito JWT authorizer.
 */
export class ApiStack extends Stack {
  readonly httpApi: apigwv2.HttpApi;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const db = new SharedDatabase(this, 'Db', { dbUser: DB_USER, dbName: props.dbName });

    // Application content: sent raw MIME (email/raw/), composer attachments (email/attachments/),
    // and later the materials library (materials/) — see common/storage.py for the prefixes the
    // IAM grants below are scoped to. The bucket lives here rather than in the Messaging stack
    // because the API Lambda is its only consumer for both email and materials, and materials are
    // not messaging; keeping it here means the materials slice needs no cross-stack reference.
    const contentBucket = new s3.Bucket(this, 'ContentBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      // Sandbox is disposable and gets torn down; prod correspondence is not.
      removalPolicy: props.envType === 'prod' ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      autoDeleteObjects: props.envType !== 'prod',
      // Composer attachments are PUT to a presigned URL **by the browser**, so the bucket is a
      // cross-origin target and needs a CORS rule — without one the preflight fails and every
      // attachment upload is blocked before it starts. This is the price of keeping attachment
      // bytes out of the API; it is not optional plumbing.
      //
      // The origins are passed in rather than derived from the Frontend stack: reaching across
      // stacks for the CloudFront domain is what silently broke the origin on 2026-07-25.
      cors: [
        {
          allowedMethods: [s3.HttpMethods.PUT],
          allowedOrigins: props.contentCorsOrigins,
          // Content-Type is signed into the presigned URL and must be sent verbatim; the rest are
          // whatever the browser adds to a PUT.
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
    });

    const environment: Record<string, string> = {
      ENV_TYPE: props.envType,
      AUTH_MODE: props.authMode,
      POWERTOOLS_SERVICE_NAME: 'speaker-tracker',
      POWERTOOLS_METRICS_NAMESPACE: 'SpeakerTracker',
      POWERTOOLS_TRACE_DISABLED: 'true',
      POWERTOOLS_LOG_LEVEL: 'INFO',
      ...db.lambdaEnv(),
    };

    // Email settings reach the runtime as plain env vars (common/{storage,secrets,imap,mail}.py).
    // The IMAP *password* is not here — it is fetched from Secrets Manager at runtime.
    const emailEnvironment: Record<string, string> = {
      CONTENT_BUCKET: contentBucket.bucketName,
      IMAP_SECRET_ID: props.email.imapSecretName,
      IMAP_HOST: props.email.imapHost,
      MAIL_FROM_ADDRESS: props.email.mailFromAddress,
      MAIL_FROM_NAME: props.email.mailFromName,
      // Sandbox only. `AUTH_MODE=dev` injects a fixed principal whose email defaults to a
      // non-deliverable placeholder, which meant a sandbox reminder could never actually arrive —
      // it would fail at SES, retry, and dead-letter. Pointing it at a real inbox is what makes
      // the reminder path exercisable before prod. common/auth.py refuses dev auth outside
      // sandbox, so this can never take effect in production however it is set.
      ...(props.authMode === 'dev' ? { DEV_USER_EMAIL: props.alarmEmail } : {}),
    };

    // Shared code bundle; CDK stages it once per unique content hash.
    const code = backendBundle();

    // One Lambda serves every API route (see ARCHITECTURE.md §1).
    const apiFn = this.pythonFunction('ApiFunction', {
      functionName: `${props.appName}-${props.envType}-api`,
      code,
      handler: 'api_handler.lambda_handler',
      memorySize: 1024,
      timeout: Duration.seconds(15),
      reservedConcurrentExecutions: props.reservedConcurrency.api,
      environment: { ...environment, ...emailEnvironment },
      logRetention: props.logRetention,
    });
    db.grantConnect(apiFn);

    // Email permissions — the API function only; the migrate function has no business sending
    // mail or reading the mailbox credential.
    //
    // Prefix-scoped to `email/*`, matching common/storage.py's constants: a key built outside the
    // documented prefixes is then denied at runtime rather than silently working. `materials/*`
    // is deliberately absent until that slice exists.
    contentBucket.grantReadWrite(apiFn, 'email/*');

    // SendRawEmail is authorized against the *identity*, so the resource is the identity ARN
    // rather than '*'. The identity is referenced by ARN and never created here.
    apiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ses:SendRawEmail'],
        resources: [props.email.sesIdentityArn],
      }),
    );

    // The trailing `-*` is required, not sloppiness: Secrets Manager appends a random six-character
    // suffix to a secret's ARN, so an exact-name ARN would never match and every fetch would be
    // denied.
    apiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [
          Stack.of(this).formatArn({
            service: 'secretsmanager',
            resource: 'secret',
            resourceName: `${props.email.imapSecretName}-*`,
            arnFormat: ArnFormat.COLON_RESOURCE_NAME,
          }),
        ],
      }),
    );

    // Migrations run on their own short-lived, dedicated connection — never the API's
    // reused one (the GET_LOCK advisory lock is only safe while the session is short-lived).
    const migrateFn = this.pythonFunction('MigrateFunction', {
      functionName: `${props.appName}-${props.envType}-migrate`,
      code,
      handler: 'handlers.migrate.lambda_handler',
      memorySize: 512,
      timeout: Duration.seconds(300),
      reservedConcurrentExecutions: props.reservedConcurrency.migrate,
      environment,
      logRetention: props.logRetention,
    });
    db.grantConnect(migrateFn);

    // ---------------------------------------------------------------------------------------
    // The IMAP poller (slice 6b).
    //
    // It lives HERE and not in `<env>-Messaging`, which is what DEV-PLAN originally said, because
    // it needs the ContentBucket for raw inbound MIME — and Messaging was deliberately built to
    // import nothing and export nothing. Putting it there would mean a cross-stack reference for
    // the bucket, which is the exact shape that broke the Frontend origin in July. Everything the
    // poller needs already exists in this stack.
    //
    // Not VPC-attached, like every other function here: the database is reached over its public
    // endpoint with IAM auth, so there is no subnet or ENI plumbing to do.
    // ---------------------------------------------------------------------------------------
    const pollFn = this.pythonFunction('ImapPollFunction', {
      functionName: `${props.appName}-${props.envType}-imap-poll`,
      code,
      handler: 'handlers.imap_poll.lambda_handler',
      memorySize: 512,
      // Generous next to the API's 15s: a UIDVALIDITY reset drains up to MAX_UIDS_PER_POLL (200)
      // messages in one invocation, each fetched over IMAP and written to S3.
      timeout: Duration.seconds(120),
      // Reserved concurrency 1 is an EFFICIENCY guard, not the correctness guarantee.
      //
      // Correctness comes from idempotency: ingest dedupes on UNIQUE(user_id, message_id), the
      // cursor cannot rewind within a UID generation, S3 writes are same-key-same-bytes, and the
      // Import move happens only after the row commits. Two overlapping invocations would
      // duplicate work, not corrupt data.
      //
      // What this prevents is pile-up: on a one-minute schedule, a poll that runs long (a hung
      // IMAP socket, slow S3) would otherwise have its successor start on top of it, every
      // minute, compounding. Throttling is the better outcome.
      //
      // It is also the WRONG unit once there is more than one mailbox. The thing that must not
      // happen twice is "polling this mailbox", and reserved concurrency is scoped to the
      // *function* — so with N users it would force every mailbox to be polled serially inside
      // one invocation. The multi-user replacement is a per-mailbox lease in the database, which
      // is the same piece of work as the mailbox→user table (see DEV-PLAN's future section);
      // adopting it is what lets this reservation be dropped.
      reservedConcurrentExecutions: props.reservedConcurrency.poll,
      environment: { ...environment, ...emailEnvironment },
      logRetention: props.logRetention,
    });
    db.grantConnect(pollFn);

    // Same prefix scoping as the API function: a key built outside `email/` is denied at runtime
    // rather than silently working.
    contentBucket.grantReadWrite(pollFn, 'email/*');

    // The mailbox password. The trailing `-*` matters for the same reason it does above — Secrets
    // Manager appends a random suffix to the ARN.
    pollFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [
          Stack.of(this).formatArn({
            service: 'secretsmanager',
            resource: 'secret',
            resourceName: `${props.email.imapSecretName}-*`,
            arnFormat: ArnFormat.COLON_RESOURCE_NAME,
          }),
        ],
      }),
    );
    // Deliberately no `ses:SendRawEmail`: the poller reads the mailbox and never sends.

    new events.Rule(this, 'ImapPollSchedule', {
      ruleName: `${props.appName}-${props.envType}-imap-poll`,
      schedule: events.Schedule.rate(Duration.minutes(1)),
      // Exactly one environment may poll a given mailbox — two pollers would race over the Import
      // folder and a dragged email would land in one database at random. The function is still
      // deployed when disabled, so it can be invoked by hand for testing.
      enabled: props.pollEnabled,
      targets: [new targets.LambdaFunction(pollFn)],
    });

    // Acceptance #11 — the project's worst failure mode is a poller that keeps running on
    // schedule, finds nothing, and stops threading mail with no error anywhere.
    //
    // The alarm watches the Lambda `Errors` metric rather than a log-metric filter on a green
    // invocation, because that rests on a *type* propagating (ImapAuthError, after one retry with
    // refreshed credentials) rather than on a log string somebody might reword. Transient network
    // failures are caught in the handler and never reach this metric.
    //
    // treatMissingData: NOT_BREACHING because a minute with no invocation is not a failure.
    const alarmTopic = new sns.Topic(this, 'ImapPollAlarmTopic', {
      topicName: `${props.appName}-${props.envType}-imap-poll-alarm`,
      displayName: 'Speaker Tracker IMAP poll failures',
    });
    // Email subscriptions require a one-time confirmation click; until it is confirmed the
    // subscription is PendingConfirmation and the alarm is silent.
    alarmTopic.addSubscription(new snsSubscriptions.EmailSubscription(props.alarmEmail));

    const pollAlarm = new cloudwatch.Alarm(this, 'ImapPollFailureAlarm', {
      alarmName: `${props.appName}-${props.envType}-imap-poll-failures`,
      alarmDescription:
        'The IMAP poller failed. Most likely the mailbox password was rotated: the handler ' +
        'retries once with a freshly fetched secret and then lets the error fail the ' +
        'invocation, which is what fires this. Inbound mail is NOT being processed until it is ' +
        'fixed. Check the function log for "credentials rejected after refresh".',
      metric: pollFn.metricErrors({ period: Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    pollAlarm.addAlarmAction(new cwActions.SnsAction(alarmTopic));

    // ---------------------------------------------------------------------------------------
    // Follow-up reminders (slice 7).
    //
    // The schedule group, the invoke role and the notify function all live HERE rather than in
    // `<env>-Messaging`, which is what DEV-PLAN originally said — the same correction the IMAP
    // poller needed above, for the same reason. Messaging was deliberately built to import and
    // export nothing, so siting these there would force this stack to take cross-stack references
    // to the group name and role ARN: the weak-reference shape that left the CloudFront origin
    // pointing at a deleted API in July. Everything needed is already in this stack.
    //
    // One EventBridge schedule per pending follow-up, named `followup-<id>` (common/scheduler.py).
    // The name is a pure function of the row id, so nothing here tracks schedule state and nothing
    // reads it back.
    // ---------------------------------------------------------------------------------------
    const followUpGroup = new scheduler.CfnScheduleGroup(this, 'FollowUpScheduleGroup', {
      name: `${props.appName}-${props.envType}-followups`,
    });

    // The reminder sender. Note what it does NOT get: no `db.grantConnect`, no database env vars,
    // no content bucket, no IMAP secret. handlers/followup_notify.py renders everything from the
    // schedule's frozen payload and never opens a connection — that is what lets it run with no
    // RDS handshake, and this is where the guarantee stops being a convention and becomes IAM. If
    // it ever grows a database need, the deploy is the right place for that to hurt.
    //
    // No reserved concurrency: a one-time schedule cannot pile up the way the 1-minute poller can,
    // and sandbox reserves sparingly to fit the account's concurrent-execution limit.
    const notifyFn = this.pythonFunction('FollowUpNotifyFunction', {
      functionName: `${props.appName}-${props.envType}-followup-notify`,
      code,
      handler: 'handlers.followup_notify.lambda_handler',
      memorySize: 256,
      timeout: Duration.seconds(30),
      environment: {
        ENV_TYPE: props.envType,
        POWERTOOLS_SERVICE_NAME: 'speaker-tracker',
        POWERTOOLS_METRICS_NAMESPACE: 'SpeakerTracker',
        POWERTOOLS_TRACE_DISABLED: 'true',
        POWERTOOLS_LOG_LEVEL: 'INFO',
        MAIL_FROM_ADDRESS: props.email.mailFromAddress,
        MAIL_FROM_NAME: props.email.mailFromName,
      },
      logRetention: props.logRetention,
    });

    // Scoped to the identity ARN, not '*', exactly as the API function's grant is.
    notifyFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ses:SendRawEmail'],
        resources: [props.email.sesIdentityArn],
      }),
    );

    // The role EventBridge Scheduler assumes to invoke the notify function. The SourceAccount
    // condition is the confused-deputy guard: without it, another account's scheduler service
    // could in principle present this role.
    const schedulerRole = new iam.Role(this, 'FollowUpSchedulerRole', {
      roleName: `${props.appName}-${props.envType}-followup-scheduler`,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': Stack.of(this).account },
        },
      }),
      description: 'Assumed by EventBridge Scheduler to invoke the follow-up reminder function',
    });
    notifyFn.grantInvoke(schedulerRole);

    // The API function manages the schedules. Deliberately no `scheduler:GetSchedule` and no
    // `scheduler:ListSchedules`: the design never reads schedule state back — every operation is
    // addressed by the derived name — so the policy says so. A future read fails in IAM rather
    // than quietly introducing the dependency the naming scheme exists to avoid.
    apiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'scheduler:CreateSchedule',
          'scheduler:UpdateSchedule',
          'scheduler:DeleteSchedule',
        ],
        resources: [
          Stack.of(this).formatArn({
            service: 'scheduler',
            resource: 'schedule',
            resourceName: `${followUpGroup.name}/*`,
            arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
          }),
        ],
      }),
    );

    // Creating a schedule that carries `schedulerRole` requires permission to pass it.
    apiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['iam:PassRole'],
        resources: [schedulerRole.roleArn],
      }),
    );

    // Where a reminder goes when every retry has failed.
    //
    // EventBridge's defaults are 185 attempts over 24 hours with NO dead-letter queue, which for
    // this payload means a failing reminder either hammers SES all day or disappears without
    // trace. common/scheduler.py sets a 5-attempt / 2-hour budget instead — a reminder that has
    // not landed by mid-morning has missed its purpose — and anything that exhausts it arrives
    // here, where the alarm below makes it somebody's problem rather than nobody's.
    const followUpDlq = new sqs.Queue(this, 'FollowUpDlq', {
      // Not "-dlq": there is no primary queue behind it. EventBridge invokes the notify function
      // directly, and this is where an invocation goes once its retries are spent — a dead-letter
      // *destination*, not the partner of a source queue. The old name sent people looking for one.
      queueName: `${props.appName}-${props.envType}-followup-failures`,
      // Long enough that a weekend failure is still there on Monday.
      retentionPeriod: Duration.days(14),
      enforceSSL: true,
    });
    followUpDlq.grantSendMessages(schedulerRole);

    // Consumes the failure queue and flags the follow-up, so the app can say the nudge did not go
    // out instead of showing a row identical to one whose email arrived. This is the ONLY part of
    // the reminder path with database access — followup-notify has none, which is what keeps the
    // happy path free of an RDS handshake. Only the failure path pays.
    const failedFn = this.pythonFunction('FollowUpFailedFunction', {
      functionName: `${props.appName}-${props.envType}-followup-failed`,
      code,
      handler: 'handlers.followup_failed.lambda_handler',
      memorySize: 512,
      timeout: Duration.seconds(30),
      environment,
      logRetention: props.logRetention,
    });
    db.grantConnect(failedFn);
    failedFn.addEventSource(
      new lambdaEventSources.SqsEventSource(followUpDlq, {
        // Failures are rare and individually interesting; batching would only delay the flag.
        batchSize: 1,
        // The handler returns batchItemFailures, so a message it cannot parse is dropped rather
        // than redelivered forever, while a database outage is retried.
        reportBatchItemFailures: true,
      }),
    );

    // The alarm watches the CONSUMER'S INVOCATIONS, not the queue depth.
    //
    // Queue depth was the obvious metric and is the wrong one once something drains the queue: a
    // message consumed within seconds may never be visible at an alarm evaluation, so the alarm
    // would sit green through exactly the failures it exists to report. An invocation of this
    // function means a reminder was dead-lettered — it cannot be missed by timing.
    const dlqAlarm = new cloudwatch.Alarm(this, 'FollowUpDlqAlarm', {
      alarmName: `${props.appName}-${props.envType}-followup-failures`,
      alarmDescription:
        'A follow-up reminder exhausted its retries and was dead-lettered. Donna did NOT get ' +
        'that nudge. The follow-up is flagged in the app and stays on her Dashboard, so nothing ' +
        'is lost — but the reminder path is broken and tomorrow\'s reminders will fail too. ' +
        'Check the followup-notify log for the cause.',
      metric: failedFn.metricInvocations({ period: Duration.minutes(5), statistic: 'Sum' }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    // Reuses the existing alarm topic rather than minting a second one: its email subscription is
    // already confirmed, and a new topic would sit unconfirmed — silently swallowing alarms — until
    // someone clicked the link. The topic's name still says imap-poll; that is worth renaming, but
    // not at the cost of a window where both alarms are mute.
    dlqAlarm.addAlarmAction(new cwActions.SnsAction(alarmTopic));

    // Added after the fact rather than in the function's `environment` above, so construct
    // ordering does not matter. These three names are what common/scheduler.py reads; when any is
    // absent it no-ops and logs a WARNING, so an un-deployed scheduler degrades to a
    // Dashboard-only reminder instead of a failed request.
    apiFn.addEnvironment('SCHEDULER_GROUP_NAME', followUpGroup.name!);
    apiFn.addEnvironment('SCHEDULER_NOTIFY_ARN', notifyFn.functionArn);
    apiFn.addEnvironment('SCHEDULER_ROLE_ARN', schedulerRole.roleArn);
    apiFn.addEnvironment('SCHEDULER_DLQ_ARN', followUpDlq.queueArn);

    // HTTP API with explicit routes (not ANY /{proxy+}), so /health can stay open and
    // the gateway rejects unknown paths itself.
    this.httpApi = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: `${props.appName}-${props.envType}-api`,
    });

    // `scopePermissionToRoute: false` is load-bearing, not a style choice. The default (true)
    // emits one AWS::Lambda::Permission **per route**, each pinned to that route's path, and they
    // all accumulate in the function's resource policy — which AWS caps at 20,480 bytes. At ~356
    // bytes a statement, 58 routes overflowed it and the deploy failed with "The final policy size
    // (20662) is bigger than the limit (20480)". With this off, CDK emits a single api-scoped
    // permission (`<apiId>/*/*/*`) shared by every route, so the policy no longer grows with the
    // route table. The grant stays pinned to this API's id; since one function already serves every
    // route, it confers nothing the per-route statements did not. Unknown paths are still rejected
    // by the gateway, because that comes from the explicit ROUTES table above, not from permissions.
    const integration = new HttpLambdaIntegration('ApiIntegration', apiFn, {
      scopePermissionToRoute: false,
    });
    const noAuth = new apigwv2.HttpNoneAuthorizer();
    // Cognito ID token: issuer = pool provider URL, audience = app client id (§6.1).
    const jwtAuthorizer = props.auth
      ? new HttpJwtAuthorizer(
          'JwtAuthorizer',
          `https://cognito-idp.${this.region}.amazonaws.com/${props.auth.userPool.userPoolId}`,
          { jwtAudience: [props.auth.userPoolClient.userPoolClientId] },
        )
      : undefined;

    for (const route of ROUTES) {
      this.httpApi.addRoutes({
        path: route.path,
        methods: [route.method],
        integration,
        authorizer: route.authRequired && jwtAuthorizer ? jwtAuthorizer : noAuth,
      });
    }

    // Apply pending migrations during the deploy, after the function is in place.
    // A failure re-raises and fails the deploy rather than leaving the schema half-applied.
    new triggers.Trigger(this, 'RunMigrations', {
      handler: migrateFn,
      executeOnHandlerChange: true,
    });
  }

  /** Create an arm64 Python function with an explicit, env-scoped log group. */
  private pythonFunction(
    id: string,
    props: {
      functionName: string;
      code: lambda.Code;
      handler: string;
      memorySize: number;
      timeout: Duration;
      reservedConcurrentExecutions?: number;
      environment: Record<string, string>;
      logRetention: logs.RetentionDays;
    },
  ): lambda.Function {
    const logGroup = new logs.LogGroup(this, `${id}Logs`, {
      logGroupName: `/aws/lambda/${props.functionName}`,
      retention: props.logRetention,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    return new lambda.Function(this, id, {
      functionName: props.functionName,
      runtime: PYTHON_RUNTIME,
      architecture: LAMBDA_ARCH,
      code: props.code,
      handler: props.handler,
      memorySize: props.memorySize,
      timeout: props.timeout,
      reservedConcurrentExecutions: props.reservedConcurrentExecutions,
      environment: props.environment,
      logGroup,
    });
  }
}
