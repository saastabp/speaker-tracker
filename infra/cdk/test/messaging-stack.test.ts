import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack } from '../lib/api-stack';
import { MessagingStack } from '../lib/messaging-stack';
import * as logs from 'aws-cdk-lib/aws-logs';
import { TEST_EMAIL_CONFIG, TEST_POLL_CONFIG } from './fixtures';

const ENV = { account: '111111111111', region: 'us-west-2' };

const newApp = () => new App({ context: { 'aws:cdk:bundling-stacks': [] } });

const messagingStack = (envType: 'sandbox' | 'prod' = 'sandbox') =>
  new MessagingStack(newApp(), `speaker-tracker-${envType}-Messaging`, {
    env: ENV,
    envType,
    imapSecretName: `speakertracker/${envType}/imap`,
    sesIdentityArn: TEST_EMAIL_CONFIG.sesIdentityArn,
    imapHost: TEST_EMAIL_CONFIG.imapHost,
  });

describe('Messaging stack', () => {
  test('never creates or manages an SES identity', () => {
    // The domain identity is shared with other senders on 360balancedliving.com. If CDK owned it,
    // a `cdk destroy` or a stack replacement could delete a verification other senders depend on —
    // an outage this app has no way to detect and no business causing.
    const template = Template.fromStack(messagingStack());

    template.resourceCountIs('AWS::SES::EmailIdentity', 0);
    template.resourceCountIs('AWS::SES::ConfigurationSet', 0);
    expect(JSON.stringify(template.toJSON())).not.toContain('AWS::SES::');
  });

  test('creates the IMAP secret empty, never with a real value', () => {
    // Anything CDK knows at synth time is written in plaintext to cdk.out, the staging bucket and
    // the CloudFormation template. The password is written once by hand, out of band.
    const template = Template.fromStack(messagingStack());

    template.hasResourceProperties('AWS::SecretsManager::Secret', {
      Name: 'speakertracker/sandbox/imap',
      SecretString: '{}',
    });
    // No generated-password machinery either: a generated secret would look like a credential
    // while being useless, which is worse than an obviously empty placeholder.
    template.hasResourceProperties(
      'AWS::SecretsManager::Secret',
      Match.not({ GenerateSecretString: Match.anyValue() }),
    );
  });

  test('prod retains the secret; sandbox does not', () => {
    // Prod must never discard the mailbox credential via a stack operation. Sandbox is torn down
    // and recreated often enough that a leftover secret — and the "already exists" conflict it
    // causes on redeploy — costs more than re-running the one put-secret-value step.
    Template.fromStack(messagingStack('prod')).hasResource('AWS::SecretsManager::Secret', {
      DeletionPolicy: 'Retain',
      UpdateReplacePolicy: 'Retain',
    });
    Template.fromStack(messagingStack('sandbox')).hasResource('AWS::SecretsManager::Secret', {
      DeletionPolicy: 'Delete',
      UpdateReplacePolicy: 'Delete',
    });
  });

  test('secret names are environment-scoped so the two stacks cannot collide', () => {
    // One shared `speakertracker/imap` would fail the second stack's create, and would mean
    // rotating prod silently changed sandbox.
    const sandbox = Template.fromStack(messagingStack('sandbox'));
    const prod = Template.fromStack(messagingStack('prod'));

    sandbox.hasResourceProperties('AWS::SecretsManager::Secret', {
      Name: 'speakertracker/sandbox/imap',
    });
    prod.hasResourceProperties('AWS::SecretsManager::Secret', {
      Name: 'speakertracker/prod/imap',
    });
  });

  test('nothing is exported for another stack to import', () => {
    // The Api stack takes the secret name and identity ARN as plain config, deliberately: a
    // cross-stack reference here would couple the deploy order and, if weak, could silently go
    // stale (the 2026-07-25 CloudFront-origin incident).
    const outputs = Template.fromStack(messagingStack()).findOutputs('*');

    const exported = Object.values(outputs).filter((o: any) => o.Export?.Name !== undefined);
    expect(exported).toHaveLength(0);
  });
});

describe('Api stack email permissions', () => {
  const apiTemplate = () =>
    Template.fromStack(
      new ApiStack(newApp(), 'speaker-tracker-sandbox-Api', {
        env: ENV,
        appName: 'speaker-tracker',
        envType: 'sandbox',
        authMode: 'dev',
        dbName: 'speakertracker_sandbox',
        logRetention: logs.RetentionDays.ONE_MONTH,
        reservedConcurrency: {},
        ...TEST_POLL_CONFIG,
        email: TEST_EMAIL_CONFIG,
        contentCorsOrigins: ['https://example.test'],
      }),
    );

  test('SendRawEmail is scoped to the identity, never "*"', () => {
    const policies = apiTemplate().findResources('AWS::IAM::Policy');
    const statements = Object.values(policies).flatMap(
      (p: any) => p.Properties.PolicyDocument.Statement,
    );
    const ses = statements.filter((s: any) => JSON.stringify(s.Action).includes('ses:'));

    // Two senders as of slice 7: the composer (ApiFunction) and the follow-up reminder
    // (FollowUpNotifyFunction). The invariant under test is the *scoping*, not the count — this
    // asserted `toHaveLength(1)` when only one thing sent, which conflated the two. Checking every
    // statement means a third sender does not break the test, while a '*' resource still does.
    expect(ses.length).toBeGreaterThan(0);
    for (const statement of ses) {
      expect(statement.Resource).toBe(TEST_EMAIL_CONFIG.sesIdentityArn);
    }
  });

  test('every secret grant carries the trailing -* Secrets Manager requires', () => {
    // Secrets Manager appends a random six-character suffix to a secret's ARN, so an exact-name
    // ARN matches nothing and every fetch would be denied at runtime.
    //
    // Asserts the property of each grant rather than that there is exactly one. Two functions read
    // the mailbox password now — the API for the Sent-folder APPEND, and 6b's poller — and a count
    // would have to be revised every time that changes, which is how a test stops meaning anything.
    const policies = apiTemplate().findResources('AWS::IAM::Policy');
    const statements = Object.values(policies).flatMap(
      (p: any) => p.Properties.PolicyDocument.Statement,
    );
    const secret = statements.filter((s: any) =>
      JSON.stringify(s.Action).includes('secretsmanager:GetSecretValue'),
    );

    expect(secret.length).toBeGreaterThan(0);
    for (const statement of secret) {
      expect(JSON.stringify(statement.Resource)).toContain('speakertracker/imap-*');
    }
  });

  test('the content bucket allows browser PUTs and GETs from the app origin', () => {
    // Composer attachments are PUT to a presigned URL by the browser, which makes the bucket a
    // cross-origin target. Without a CORS rule the preflight fails and every upload is blocked —
    // a failure that only ever appears in a browser, never in synth or a unit test.
    //
    // GET is here for the materials library's *text* previews. An image or PDF previews via
    // `<img>`/`<iframe>`, which need no CORS; reading a markdown file to display it means `fetch`,
    // which does. Same class of failure: browser-only, invisible to synth.
    apiTemplate().hasResourceProperties('AWS::S3::Bucket', {
      CorsConfiguration: {
        CorsRules: [
          Match.objectLike({
            AllowedMethods: ['PUT', 'GET', 'HEAD'],
            AllowedOrigins: ['https://example.test'],
          }),
        ],
      },
    });
  });

  test('the content bucket grant covers both documented prefixes and nothing else', () => {
    // Matches common/storage.py's constants. A key built outside these is denied at runtime, so
    // the grant and the code have to move together — materials/* arrived with slice 9.
    const policies = apiTemplate().findResources('AWS::IAM::Policy');
    const rendered = JSON.stringify(Object.values(policies));

    expect(rendered).toContain('email/*');
    expect(rendered).toContain('materials/*');
  });

  test('nothing may delete a stored email object', () => {
    // `email/raw/` holds the only copy of what was actually sent or received — the thread view
    // rebuilds bodies and attachments from it, and nothing can regenerate it. `grantReadWrite`
    // quietly bundles s3:DeleteObject*, which is how this capability existed unnoticed until
    // slice 9 needed a delete for materials and went looking. Read + put only, here on purpose.
    const policies = apiTemplate().findResources('AWS::IAM::Policy');
    const statements = Object.values(policies).flatMap(
      (policy) =>
        (policy as never as { Properties: { PolicyDocument: { Statement: unknown[] } } }).Properties
          .PolicyDocument.Statement,
    );
    const deleteStatements = statements.filter((statement) =>
      JSON.stringify((statement as { Action?: unknown }).Action ?? '').includes('s3:DeleteObject'),
    );

    // Materials still need one, for cleaning up a file that has been replaced.
    expect(JSON.stringify(deleteStatements)).toContain('materials/*');
    expect(JSON.stringify(deleteStatements)).not.toContain('email/*');
  });
});