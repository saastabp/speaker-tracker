import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpJwtAuthorizer } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
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

/** Slice-1 route table. `/health` stays open for uptime checks; `/catalogs` is protected in prod. */
const ROUTES: RouteDef[] = [
  { method: apigwv2.HttpMethod.GET, path: '/health', authRequired: false },
  { method: apigwv2.HttpMethod.GET, path: '/catalogs', authRequired: true },
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

    const environment: Record<string, string> = {
      ENV_TYPE: props.envType,
      AUTH_MODE: props.authMode,
      POWERTOOLS_SERVICE_NAME: 'speaker-tracker',
      POWERTOOLS_METRICS_NAMESPACE: 'SpeakerTracker',
      POWERTOOLS_TRACE_DISABLED: 'true',
      POWERTOOLS_LOG_LEVEL: 'INFO',
      ...db.lambdaEnv(),
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
      environment,
      logRetention: props.logRetention,
    });
    db.grantConnect(apiFn);

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

    const integration = new HttpLambdaIntegration('ApiIntegration', apiFn);
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
