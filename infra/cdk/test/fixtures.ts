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