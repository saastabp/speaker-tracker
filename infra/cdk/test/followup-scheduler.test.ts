/**
 * The follow-up reminder infrastructure (slice 7, checkpoint I).
 *
 * These assert the two things a later edit could undo silently, and that no test of the happy path
 * would notice:
 *
 * 1. **The notify function cannot reach the database.** Its whole design — rendering the email from
 *    the schedule's frozen payload, never reading the row — is what lets it run with no RDS
 *    handshake, and is why every edit to a rendered field must cancel and recreate the schedule.
 *    A backend test already scans the module's imports; this is the same guarantee in IAM, where
 *    adding `db.grantConnect` would otherwise pass unremarked.
 * 2. **The schedule permissions are group-scoped and write-only.** Nothing in this app reads
 *    schedule state back — every operation is addressed by the derived `followup-<id>` name — so a
 *    `scheduler:GetSchedule` appearing here would mean someone had introduced the dependency the
 *    naming scheme exists to avoid.
 */
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack, ApiStackProps } from '../lib/api-stack';
import { TEST_EMAIL_CONFIG, TEST_POLL_CONFIG } from './fixtures';

const ENV = { account: '111111111111', region: 'us-west-2' };
/** Derived from the fixture, so the assertion cannot drift from what the stack is given. */
const TEST_ALARM_EMAIL = TEST_POLL_CONFIG.alarmEmail;

const props = (overrides: Partial<ApiStackProps> = {}): ApiStackProps => ({
  env: ENV,
  appName: 'speaker-tracker',
  envType: 'sandbox',
  authMode: 'dev',
  dbName: 'speakertracker_sandbox',
  logRetention: 30,
  reservedConcurrency: { poll: 1 },
  email: TEST_EMAIL_CONFIG,
  contentCorsOrigins: ['http://localhost:5173'],
  ...TEST_POLL_CONFIG,
  ...overrides,
});

const template = (): Template =>
  Template.fromStack(new ApiStack(new App(), 'TestApi', props()));

/** The notify Lambda, found by handler rather than logical id. */
const notifyFunction = (t: Template) =>
  Object.values(
    t.findResources('AWS::Lambda::Function', {
      Properties: { Handler: 'handlers.followup_notify.lambda_handler' },
    }),
  )[0] as any;

/** Every IAM policy statement in the stack, flattened. */
const allStatements = (t: Template) =>
  Object.values(t.findResources('AWS::IAM::Policy')).flatMap(
    (p: any) => p.Properties.PolicyDocument.Statement,
  );

describe('follow-up reminder infrastructure', () => {
  test('the schedule group is created in this stack, not Messaging', () => {
    template().hasResourceProperties('AWS::Scheduler::ScheduleGroup', {
      Name: 'speaker-tracker-sandbox-followups',
    });
  });

  test('the notify function exists and sends mail', () => {
    const fn = notifyFunction(template());
    expect(fn).toBeDefined();
    expect(fn.Properties.MemorySize).toBe(256);

    const ses = allStatements(template()).filter((s: any) =>
      JSON.stringify(s.Action).includes('ses:SendRawEmail'),
    );
    expect(ses.length).toBeGreaterThan(0);
    for (const statement of ses) {
      expect(statement.Resource).toBe(TEST_EMAIL_CONFIG.sesIdentityArn);
    }
  });

  test('the notify function carries no database configuration', () => {
    const variables = notifyFunction(template()).Properties.Environment.Variables;
    for (const name of ['DB_HOST', 'DB_PORT', 'DB_USER', 'DB_NAME']) {
      expect(variables).not.toHaveProperty(name);
    }
    // It needs the sender identity, and nothing beyond it.
    expect(variables.MAIL_FROM_ADDRESS).toBe(TEST_EMAIL_CONFIG.mailFromAddress);
  });

  test('the notify function has no rds-db:connect grant', () => {
    const t = template();
    const notifyRoleRef = notifyFunction(t).Properties.Role['Fn::GetAtt'][0];

    const dbGrants = Object.values(t.findResources('AWS::IAM::Policy')).filter((p: any) => {
      const grantsConnect = JSON.stringify(p.Properties.PolicyDocument.Statement).includes(
        'rds-db:connect',
      );
      const attachedToNotify = JSON.stringify(p.Properties.Roles ?? []).includes(notifyRoleRef);
      return grantsConnect && attachedToNotify;
    });
    expect(dbGrants).toHaveLength(0);
  });

  test('schedule permissions are scoped to the group, never "*"', () => {
    const schedulerStatements = allStatements(template()).filter((s: any) =>
      JSON.stringify(s.Action).includes('scheduler:'),
    );
    expect(schedulerStatements.length).toBeGreaterThan(0);

    for (const statement of schedulerStatements) {
      expect(statement.Resource).not.toBe('*');
      expect(JSON.stringify(statement.Resource)).toContain(
        'schedule/speaker-tracker-sandbox-followups/*',
      );
    }
  });

  test('the app is granted no way to read schedule state back', () => {
    const actions = JSON.stringify(
      allStatements(template())
        .filter((s: any) => JSON.stringify(s.Action).includes('scheduler:'))
        .map((s: any) => s.Action),
    );
    expect(actions).toContain('scheduler:CreateSchedule');
    expect(actions).toContain('scheduler:DeleteSchedule');
    expect(actions).not.toContain('scheduler:GetSchedule');
    expect(actions).not.toContain('scheduler:ListSchedules');
  });

  test('the scheduler role is assumable only by scheduler, only from this account', () => {
    template().hasResourceProperties('AWS::IAM::Role', {
      AssumeRolePolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Principal: { Service: 'scheduler.amazonaws.com' },
            Condition: { StringEquals: { 'aws:SourceAccount': ENV.account } },
          }),
        ]),
      },
    });
  });

  test('failed reminders are dead-lettered rather than lost', () => {
    template().hasResourceProperties('AWS::SQS::Queue', {
      QueueName: 'speaker-tracker-sandbox-followup-failures',
    });
  });

  test('the alarm watches the consumer, not the queue depth', () => {
    // Queue depth is the obvious metric and the wrong one: the consumer drains the queue in
    // seconds, so a depth alarm can sit green through the very failures it exists to report.
    template().hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'speaker-tracker-sandbox-followup-failures',
      Threshold: 1,
      MetricName: 'Invocations',
      Namespace: 'AWS/Lambda',
    });
  });

  test('the failure consumer has database access — and is the only reminder function that does', () => {
    const t = template();
    const roleOf = (handler: string) =>
      (
        Object.values(
          t.findResources('AWS::Lambda::Function', { Properties: { Handler: handler } }),
        )[0] as any
      ).Properties.Role['Fn::GetAtt'][0];

    const consumerRole = roleOf('handlers.followup_failed.lambda_handler');
    const notifyRole = roleOf('handlers.followup_notify.lambda_handler');

    const connectPolicies = Object.values(t.findResources('AWS::IAM::Policy')).filter((p: any) =>
      JSON.stringify(p.Properties.PolicyDocument.Statement).includes('rds-db:connect'),
    );
    const rolesWithDb = JSON.stringify(connectPolicies.map((p: any) => p.Properties.Roles ?? []));

    expect(rolesWithDb).toContain(consumerRole);
    expect(rolesWithDb).not.toContain(notifyRole);
  });

  test('the consumer is triggered by the failure queue and reports partial failures', () => {
    template().hasResourceProperties('AWS::Lambda::EventSourceMapping', {
      FunctionResponseTypes: ['ReportBatchItemFailures'],
    });
  });

  test('the scheduler role may write to the dead-letter queue', () => {
    // Without this grant EventBridge silently cannot dead-letter, and the alarm above could never
    // fire however many reminders failed.
    const sendsToDlq = allStatements(template()).filter((s: any) =>
      JSON.stringify(s.Action).includes('sqs:SendMessage'),
    );
    expect(sendsToDlq.length).toBeGreaterThan(0);
  });

  test('sandbox gets a deliverable dev principal address', () => {
    // The default is non-deliverable, which made the reminder path untestable in sandbox.
    const apiFn = Object.values(
      template().findResources('AWS::Lambda::Function', {
        Properties: { Handler: 'api_handler.lambda_handler' },
      }),
    )[0] as any;
    expect(apiFn.Properties.Environment.Variables.DEV_USER_EMAIL).toBe(TEST_ALARM_EMAIL);
  });

  test('prod never gets a dev principal address, however config is set', () => {
    const prod = Template.fromStack(
      new ApiStack(new App(), 'TestProd', props({ envType: 'prod', authMode: 'cognito' })),
    );
    const apiFn = Object.values(
      prod.findResources('AWS::Lambda::Function', {
        Properties: { Handler: 'api_handler.lambda_handler' },
      }),
    )[0] as any;
    expect(apiFn.Properties.Environment.Variables).not.toHaveProperty('DEV_USER_EMAIL');
  });

  test('PassRole is limited to the scheduler role', () => {
    const passRole = allStatements(template()).filter((s: any) =>
      JSON.stringify(s.Action).includes('iam:PassRole'),
    );
    expect(passRole.length).toBeGreaterThan(0);
    for (const statement of passRole) {
      expect(statement.Resource).not.toBe('*');
    }
  });
});