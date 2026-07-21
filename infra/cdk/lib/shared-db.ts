import { Stack } from 'aws-cdk-lib';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

/**
 * SSM parameter paths for the shared `jobtracker-db`, published by the
 * job-tracker app. Change these here if job-tracker ever renames them.
 */
const SSM_PATH = {
  endpoint: '/jobtracker/data/db-endpoint',
  port: '/jobtracker/data/db-port',
  resourceId: '/jobtracker/data/db-resource-id',
} as const;

export interface SharedDatabaseProps {
  /** IAM DB user to connect as (e.g. `speakertracker_app`). */
  readonly dbUser: string;
  /** Schema selected on connect (`speakertracker` | `speakertracker_sandbox`). */
  readonly dbName: string;
}

/**
 * Reference to the shared `jobtracker-db` RDS instance. The instance is never
 * constructed here — we only read its coordinates from `/jobtracker/data/*`
 * (see ARCHITECTURE.md §6) and grant scoped IAM access. SSM reads resolve at
 * deploy time, so `cdk synth` needs no AWS credentials.
 */
export class SharedDatabase extends Construct {
  private readonly host: string;
  private readonly port: string;
  private readonly resourceId: string;
  private readonly dbUser: string;
  private readonly dbName: string;

  constructor(scope: Construct, id: string, props: SharedDatabaseProps) {
    super(scope, id);
    // Deploy-time SSM tokens — never resolved at synth, never embedded in the template.
    this.host = ssm.StringParameter.valueForStringParameter(this, SSM_PATH.endpoint);
    this.port = ssm.StringParameter.valueForStringParameter(this, SSM_PATH.port);
    this.resourceId = ssm.StringParameter.valueForStringParameter(this, SSM_PATH.resourceId);
    this.dbUser = props.dbUser;
    this.dbName = props.dbName;
  }

  /**
   * Env vars for a DB-touching Lambda. `DB_REGION` is omitted deliberately —
   * `common/db.py` falls back to `AWS_REGION`, which Lambda sets to the
   * function's own region (us-west-2, same as the DB).
   */
  lambdaEnv(): Record<string, string> {
    return {
      DB_HOST: this.host,
      DB_PORT: this.port,
      DB_USER: this.dbUser,
      DB_NAME: this.dbName,
    };
  }

  /**
   * Grant a function's role `rds-db:connect`, scoped to this DB user on this
   * instance only — the ARN embeds the DbiResourceId, so the grant does not
   * widen if a same-named user is created on another instance.
   */
  grantConnect(grantee: iam.IGrantable): void {
    const stack = Stack.of(this);
    grantee.grantPrincipal.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ['rds-db:connect'],
        resources: [
          `arn:aws:rds-db:${stack.region}:${stack.account}:dbuser:${this.resourceId}/${this.dbUser}`,
        ],
      }),
    );
  }
}
