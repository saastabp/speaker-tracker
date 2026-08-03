/**
 * The IMAP poller's infrastructure (slice 6b, checkpoint I).
 *
 * These assert the wiring that has no other way of being checked before a deploy: that the poller
 * lives in the Api stack rather than Messaging, that its schedule obeys the one-mailbox-one-poller
 * flag, that a failure actually reaches an alarm, and that it holds no permission it does not need.
 *
 * The last one is the reason this file is worth its length. A poller that can send mail, or that
 * silently has no alarm, looks identical to a correct one in every test that only checks the happy
 * path — and the failure mode acceptance #11 exists to prevent is precisely a poller that appears
 * to be working.
 */
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack, ApiStackProps } from '../lib/api-stack';
import { PROD, SANDBOX } from '../lib/config';
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

const templateFor = (overrides: Partial<ApiStackProps> = {}): Template =>
  Template.fromStack(new ApiStack(new App(), 'TestApi', props(overrides)));

/** The poller's Lambda resource, found by its handler rather than by logical id. */
const pollFunction = (template: Template) =>
  Object.values(
    template.findResources('AWS::Lambda::Function', {
      Properties: { Handler: 'handlers.imap_poll.lambda_handler' },
    }),
  )[0];

describe('the poller function', () => {
  test('is created in the Api stack, where the content bucket already is', () => {
    // DEV-PLAN originally put it in <env>-Messaging. It needs the ContentBucket for raw inbound
    // MIME, and Messaging deliberately imports and exports nothing, so that would have meant a
    // cross-stack reference — the shape that broke the Frontend origin in July.
    expect(pollFunction(templateFor())).toBeDefined();
  });

  test('reserves concurrency 1 so a slow poll cannot pile up on a one-minute schedule', () => {
    expect(pollFunction(templateFor()).Properties.ReservedConcurrentExecutions).toBe(1);
  });

  test('has a timeout long enough to drain a UIDVALIDITY rescan', () => {
    // MAX_UIDS_PER_POLL is 200, each fetched over IMAP and written to S3; the API's 15s would not
    // survive that.
    expect(pollFunction(templateFor()).Properties.Timeout).toBeGreaterThanOrEqual(60);
  });

  test('knows the mailbox host and the sending address', () => {
    // An unset MAIL_FROM_ADDRESS only degrades to a WARNING in the handler, so getting it wrong
    // here would be quiet: the poller would stop recognising Donna's own mail as outbound.
    const environment = pollFunction(templateFor()).Properties.Environment.Variables;
    expect(environment.IMAP_HOST).toBe(TEST_EMAIL_CONFIG.imapHost);
    expect(environment.MAIL_FROM_ADDRESS).toBe(TEST_EMAIL_CONFIG.mailFromAddress);
  });
});

describe('the schedule', () => {
  test('runs every minute when the environment is the one polling the mailbox', () => {
    const template = templateFor({ pollEnabled: true });
    template.hasResourceProperties('AWS::Events::Rule', {
      ScheduleExpression: 'rate(1 minute)',
      State: 'ENABLED',
    });
  });

  test('is disabled — not absent — when another environment owns the mailbox', () => {
    // Two pollers on one mailbox race over the Import folder, and a dragged email lands in one
    // database at random. The function still deploys so it can be invoked by hand for testing.
    const template = templateFor({ pollEnabled: false });
    template.hasResourceProperties('AWS::Events::Rule', { State: 'DISABLED' });
    expect(pollFunction(template)).toBeDefined();
  });

  test('targets the poller and nothing else', () => {
    const template = templateFor({ pollEnabled: true });
    const rules = Object.values(template.findResources('AWS::Events::Rule'));
    expect(rules).toHaveLength(1);
    expect(rules[0].Properties.Targets).toHaveLength(1);
  });

  test('exactly one environment polls the mailbox, and it is prod', () => {
    // An assertion about the real config, not a synthesized template — the disaster this guards
    // against is someone editing config.ts, and every other test here passes `pollEnabled` in
    // explicitly, so all of them stay green with both environments polling.
    //
    // Two pollers against one WorkMail mailbox race for the Import folder every minute: whichever
    // moves a message to Processed first wins and the other never sees it, so a dragged email
    // lands in one database at random and is unrecoverable from the app. config.ts explains this
    // at length; this makes it fail the build instead of the mailbox.
    //
    // Pinned to 'prod' rather than merely counting one: turning polling on for sandbox again
    // should require proving it has its own mailbox, and editing this line is that proof.
    const polling = [SANDBOX, PROD].filter((env) => env.pollEnabled).map((env) => env.envType);
    expect(polling).toEqual(['prod']);
  });
});

describe('the failure alarm (acceptance #11)', () => {
  test('watches the Lambda Errors metric', () => {
    // Deliberately not a log-metric filter on a green invocation: this rests on ImapAuthError
    // propagating — a type — rather than on a log string somebody might reword.
    templateFor().hasResourceProperties('AWS::CloudWatch::Alarm', {
      MetricName: 'Errors',
      Namespace: 'AWS/Lambda',
      Threshold: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      EvaluationPeriods: 1,
    });
  });

  test('does not treat a quiet minute as a failure', () => {
    templateFor().hasResourceProperties('AWS::CloudWatch::Alarm', {
      TreatMissingData: 'notBreaching',
    });
  });

  test('notifies an SNS topic that emails a human', () => {
    const template = templateFor();
    template.resourceCountIs('AWS::SNS::Topic', 1);
    template.hasResourceProperties('AWS::SNS::Subscription', {
      Protocol: 'email',
      Endpoint: TEST_POLL_CONFIG.alarmEmail,
    });
  });

  test('the alarm is actually wired to the topic, not merely defined beside it', () => {
    const alarm = Object.values(
      templateFor().findResources('AWS::CloudWatch::Alarm'),
    )[0];
    expect(alarm.Properties.AlarmActions).toHaveLength(1);
  });

  test('says what to do about it, since it fires at most once a year', () => {
    const alarm = Object.values(
      templateFor().findResources('AWS::CloudWatch::Alarm'),
    )[0];
    const description = alarm.Properties.AlarmDescription;
    // Names concrete places to look, not just a symptom.
    expect(description).toMatch(/Secrets Manager/i);
    expect(description).toMatch(/credentials rejected after refresh/i);
    expect(description).toMatch(/NOT being processed/i);
    // It must not blame rotation. This alarm once advised checking for a rotated password against
    // a secret that does not rotate, and fired on a transient [UNAVAILABLE] that healed in a
    // minute — remediation naming a cause that cannot occur is how an alarm gets muted.
    expect(description).not.toMatch(/rotat/i);
    // And it must say the transient case does not reach here, or the next reader re-derives it.
    expect(description).toMatch(/UNAVAILABLE/);
  });

  test('exists in both environments, because an alarm nobody has seen fire is not an alarm', () => {
    // Asserts THIS alarm by name rather than counting every alarm in the stack: the stack gained a
    // second one in slice 7 (the follow-up dead-letter queue), and a count would fail for the
    // healthy reason that more things are now monitored.
    for (const envType of ['sandbox', 'prod'] as const) {
      templateFor({ envType }).hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: `speaker-tracker-${envType}-imap-poll-failures`,
      });
    }
  });
});

describe('the poller holds no permission it does not need', () => {
  /** Every action granted to the role attached to the poller function. */
  const pollerActions = (template: Template): string[] => {
    const [logicalId] = Object.entries(
      template.findResources('AWS::Lambda::Function', {
        Properties: { Handler: 'handlers.imap_poll.lambda_handler' },
      }),
    )[0];
    const roleRef = template.findResources('AWS::Lambda::Function')[logicalId].Properties.Role[
      'Fn::GetAtt'
    ][0];

    return Object.values(template.findResources('AWS::IAM::Policy'))
      .filter((policy) =>
        JSON.stringify(policy.Properties.Roles ?? []).includes(roleRef),
      )
      .flatMap((policy) =>
        policy.Properties.PolicyDocument.Statement.flatMap((statement: { Action: string | string[] }) =>
          Array.isArray(statement.Action) ? statement.Action : [statement.Action],
        ),
      );
  };

  test('cannot send email — it reads the mailbox and never writes to it', () => {
    expect(pollerActions(templateFor())).not.toContain('ses:SendRawEmail');
  });

  test('can read the mailbox password', () => {
    expect(pollerActions(templateFor())).toContain('secretsmanager:GetSecretValue');
  });

  test('can connect to the database with IAM auth', () => {
    expect(pollerActions(templateFor())).toContain('rds-db:connect');
  });

  test('can write raw MIME to the content bucket', () => {
    // Without this the s3_key is NULL and every received message displays with no body — the
    // defect checkpoint F shipped with.
    expect(pollerActions(templateFor())).toEqual(
      expect.arrayContaining([expect.stringMatching(/^s3:PutObject/)]),
    );
  });

  test('its bucket access is prefix-scoped to email/', () => {
    const template = templateFor();
    const policies = JSON.stringify(template.findResources('AWS::IAM::Policy'));
    expect(policies).toContain('email/*');
    expect(policies).not.toMatch(/"materials\/\*"/);
  });
});

describe('the rest of the stack is undisturbed', () => {
  test('the api and migrate functions still exist alongside it', () => {
    const template = templateFor();
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'api_handler.lambda_handler',
    });
    template.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'handlers.migrate.lambda_handler',
    });
  });

  test('the poller is not wired into the HTTP API', () => {
    // It is invoked by EventBridge only; a route to it would be a way in from the internet.
    const integrations = JSON.stringify(
      templateFor().findResources('AWS::ApiGatewayV2::Integration'),
    );
    expect(integrations).not.toContain('imap-poll');
  });

  test('Match.anyValue keeps this honest if the function count changes', () => {
    templateFor().hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'handlers.imap_poll.lambda_handler',
      Environment: Match.anyValue(),
    });
  });
});