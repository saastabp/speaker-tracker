/**
 * Cognito's own mail must be deliverable, and its failures must be visible.
 *
 * Both assertions exist because of a real lockout: Donna's invitation was sent through
 * `COGNITO_DEFAULT` (from `no-reply@verificationemail.com`, ~50/day) and never arrived. That left
 * her with no route in at all — Cognito refuses a password reset for a user who has never set one,
 * so "forgot password" silently sends nothing. And no log anywhere recorded the attempt: not
 * CloudTrail (hosted-UI events carry no error detail), not our Lambdas (auth never reaches them),
 * not the pool (logging is off by default).
 *
 * Auth mail is the one category with no in-app fallback. If it does not arrive, only an admin can
 * help.
 */
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AuthStack } from '../lib/auth-stack';
import { TEST_AUTH_EMAIL } from './fixtures';

const ENV = { account: '111111111111', region: 'us-west-2' };

const template = () =>
  Template.fromStack(
    new AuthStack(new App(), 'TestAuth', {
      env: ENV,
      appUrl: 'https://speaker-tracker.example.com',
      cognitoDomainPrefix: 'speakertracker-test',
      authEmail: TEST_AUTH_EMAIL,
    }),
  );

describe('auth email', () => {
  test('sends through SES, never Cognito default', () => {
    // `COGNITO_DEFAULT` is the failure mode, not merely a lesser option: it caps around 50/day and
    // sends from an address with no relationship to this domain, so it is routinely spam-filtered.
    template().hasResourceProperties('AWS::Cognito::UserPool', {
      EmailConfiguration: Match.objectLike({
        EmailSendingAccount: 'DEVELOPER',
        SourceArn: Match.anyValue(),
      }),
    });
  });

  test('sends from an address that is not the mailbox the poller reads', () => {
    // Auth mail from `MAIL_FROM_ADDRESS` would put bounces and stray replies into
    // `Speaker Tracker/Import`, where 6b's poller would process them as correspondence.
    const email = Object.values(template().findResources('AWS::Cognito::UserPool'))[0].Properties
      .EmailConfiguration;
    expect(email.From).toContain(TEST_AUTH_EMAIL.fromAddress);
    expect(email.From).not.toContain('sender@example.com'); // TEST_EMAIL_CONFIG.mailFromAddress
  });
});

describe('auth logging', () => {
  test('undeliverable notifications are logged', () => {
    template().hasResourceProperties('AWS::Cognito::LogDeliveryConfiguration', {
      LogConfigurations: Match.arrayWith([
        Match.objectLike({ EventSource: 'userNotification', LogLevel: 'ERROR' }),
      ]),
    });
  });

  test('the log group is retained long enough to investigate a report', () => {
    // A user reports "I never got the email" days later, not minutes.
    template().hasResourceProperties('AWS::Logs::LogGroup', { RetentionInDays: 90 });
  });

  test('userAuthEvents stays off — it would silently require the PLUS feature plan', () => {
    // Deliberate: PLUS is a standing per-user cost for a diagnostic used about once a year. If
    // someone adds it, the tier must change too, and this failing is the reminder.
    const configs = Object.values(
      template().findResources('AWS::Cognito::LogDeliveryConfiguration'),
    )[0].Properties.LogConfigurations;
    expect(configs.map((c: { EventSource: string }) => c.EventSource)).toEqual(['userNotification']);
    template().hasResourceProperties('AWS::Cognito::UserPool', {
      UserPoolTier: 'ESSENTIALS',
    });
  });
});