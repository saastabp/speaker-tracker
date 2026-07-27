import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack } from '../lib/api-stack';
import { MessagingStack } from '../lib/messaging-stack';
import * as logs from 'aws-cdk-lib/aws-logs';
import { TEST_EMAIL_CONFIG } from './fixtures';

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

    expect(ses).toHaveLength(1);
    expect(ses[0].Resource).toBe(TEST_EMAIL_CONFIG.sesIdentityArn);
  });

  test('the secret grant carries the trailing -* Secrets Manager requires', () => {
    // Secrets Manager appends a random six-character suffix to a secret's ARN, so an exact-name
    // ARN matches nothing and every fetch would be denied at runtime.
    const policies = apiTemplate().findResources('AWS::IAM::Policy');
    const statements = Object.values(policies).flatMap(
      (p: any) => p.Properties.PolicyDocument.Statement,
    );
    const secret = statements.filter((s: any) =>
      JSON.stringify(s.Action).includes('secretsmanager:GetSecretValue'),
    );

    expect(secret).toHaveLength(1);
    expect(JSON.stringify(secret[0].Resource)).toContain('speakertracker/imap-*');
  });

  test('the content bucket allows browser PUTs from the app origin', () => {
    // Composer attachments are PUT to a presigned URL by the browser, which makes the bucket a
    // cross-origin target. Without a CORS rule the preflight fails and every upload is blocked —
    // a failure that only ever appears in a browser, never in synth or a unit test.
    apiTemplate().hasResourceProperties('AWS::S3::Bucket', {
      CorsConfiguration: {
        CorsRules: [
          Match.objectLike({
            AllowedMethods: ['PUT'],
            AllowedOrigins: ['https://example.test'],
          }),
        ],
      },
    });
  });

  test('the content bucket grant is prefix-scoped to email/*', () => {
    // Matches common/storage.py's prefixes; materials/* is deliberately absent until that slice.
    const policies = apiTemplate().findResources('AWS::IAM::Policy');
    const rendered = JSON.stringify(Object.values(policies));

    expect(rendered).toContain('email/*');
    expect(rendered).not.toContain('materials/*');
  });
});