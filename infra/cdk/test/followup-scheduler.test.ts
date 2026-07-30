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