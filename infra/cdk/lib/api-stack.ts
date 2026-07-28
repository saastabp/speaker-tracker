import { ArnFormat, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpJwtAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
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
  readonly reservedConcurrency: { readonly api?: number; readonly migrate?: number };
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
