/**
 * Shared props for stack tests.
 *
 * The real values live in `lib/config.ts` and reach the stacks through `bin/app.ts`; tests use
 * these stand-ins so an assertion never depends on a production ARN, and so adding a required
 * prop means editing one file rather than every test that builds a stack.
 */
export const TEST_EMAIL_CONFIG = {
  sesIdentityArn: 'arn:aws:ses:us-east-1:111111111111:identity/example.com',
  imapHost: 'imap.example.test',
  imapSecretName: 'speakertracker/imap',
  mailFromAddress: 'sender@example.com',
  mailFromName: 'Test Sender',
} as const;

/** Poller wiring (slice 6b). Spread into `ApiStackProps` by every test that builds the stack.
 *
 *  `pollEnabled` is false by default so the shared fixture never implies a scheduled poller;
 *  `imap-poll.test.ts` overrides it where the schedule itself is under test. */
export const TEST_POLL_CONFIG = {
  pollEnabled: false,
  alarmEmail: 'alarms@example.test',
} as const;

/** SES sender for Cognito's invitation and password-reset mail (`AuthStackProps.authEmail`).
 *
 *  A distinct address from `TEST_EMAIL_CONFIG.mailFromAddress` on purpose — mirroring production,
 *  where auth mail must not come from the mailbox the IMAP poller reads. */
export const TEST_AUTH_EMAIL = {
  fromAddress: 'no-reply@example.com',
  fromName: 'Test App',
  sesRegion: 'us-east-1',
  sesVerifiedDomain: 'example.com',
} as const;