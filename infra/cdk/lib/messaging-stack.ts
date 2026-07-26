import { CfnOutput, RemovalPolicy, SecretValue, Stack, StackProps } from 'aws-cdk-lib';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface MessagingStackProps extends StackProps {
  readonly envType: 'sandbox' | 'prod';
  /** Secrets Manager name for the IMAP credentials, e.g. `speakertracker/imap`. */
  readonly imapSecretName: string;
  /** SES domain identity used for sending. Recorded as an output for operator reference only —
   *  this stack never creates or modifies the identity. */
  readonly sesIdentityArn: string;
  /** WorkMail IMAP endpoint, recorded as an output alongside the secret it pairs with. */
  readonly imapHost: string;
}

/**
 * Messaging: the mailbox credential, and nothing else.
 *
 * Two things this stack deliberately does **not** do.
 *
 * **It never creates the SES identity.** `360balancedliving.com` is verified in this account
 * already (DKIM SUCCESS, signing enabled) and is shared with other senders on the domain. A
 * CDK-owned `EmailIdentity` would put that verification under this stack's lifecycle, so a
 * `cdk destroy` — or a stack replacement — could delete a verification other senders depend on.
 * The Api stack references the identity by ARN when granting `ses:SendRawEmail`; nothing here
 * touches it. `test/messaging-stack.test.ts` asserts no `AWS::SES::EmailIdentity` is synthesized.
 *
 * **It never sees the IMAP password.** The secret is created with a `{}` placeholder and the real
 * value is written once, by hand, with `aws secretsmanager put-secret-value`. Anything CDK knows
 * at synth time is baked in plaintext into `cdk.out`, the CDK staging bucket, and the
 * CloudFormation template — reading a gitignored config file here would force
 * `SecretValue.unsafePlainText()` and do exactly that. CloudFormation only applies
 * `SecretString` on create, so later deploys leave the operator-written value alone.
 *
 * **Nothing imports from this stack.** The Api stack takes the secret *name* and identity ARN as
 * plain config constants rather than cross-stack references, so the two stacks deploy in any
 * order and deleting one cannot silently break the other. That is a direct response to the
 * 2026-07-25 incident where a weak cross-stack reference left the CloudFront origin pointing at a
 * deleted API with no error until a request 502'd.
 */
export class MessagingStack extends Stack {
  readonly imapSecret: secretsmanager.ISecret;

  constructor(scope: Construct, id: string, props: MessagingStackProps) {
    super(scope, id, props);

    this.imapSecret = new secretsmanager.Secret(this, 'ImapSecret', {
      secretName: props.imapSecretName,
      description:
        'WorkMail IMAP credentials for Speaker Tracker. Created empty by CDK; the value is ' +
        'written once with `aws secretsmanager put-secret-value`. Never populated at synth time.',
      // `unsafePlainText` is correct *here* and nowhere else in this stack: `{}` is a placeholder,
      // not a credential, so baking it into the template costs nothing. The same call applied to a
      // real password is precisely the mistake this stack exists to avoid.
      secretStringValue: SecretValue.unsafePlainText('{}'),
      // Same split as the Api stack's content bucket: prod keeps its data, sandbox is disposable.
      //
      // **prod = RETAIN** — the mailbox credential must never be discarded by a stack operation.
      // The cost is that deleting the prod stack leaves the secret behind, so a later redeploy
      // fails with "already exists". Recover deliberately, never reflexively:
      //   aws secretsmanager delete-secret --secret-id speakertracker/prod/imap \
      //     --force-delete-without-recovery --profile brian-admin --region us-west-2
      //
      // **sandbox = DESTROY** — sandbox stacks do get torn down and recreated, and a name
      // conflict on every redeploy is worse than re-running the one `put-secret-value` step.
      //
      // Note neither `AWS::SecretsManager::Secret` nor CDK's L2 exposes a recovery-window or
      // force-delete knob, so the deletion semantics are whatever CloudFormation does internally
      // (observed: immediate). Don't assume the DeleteSecret API's default window applies here.
      removalPolicy: props.envType === 'prod' ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
    });

    new CfnOutput(this, 'ImapSecretName', {
      value: props.imapSecretName,
      description: 'Write the value once: aws secretsmanager put-secret-value --secret-id <this>',
    });
    new CfnOutput(this, 'ImapHost', { value: props.imapHost });
    new CfnOutput(this, 'SesIdentityArn', {
      value: props.sesIdentityArn,
      description: 'Referenced by the Api stack for ses:SendRawEmail. Not managed by this stack.',
    });
  }
}