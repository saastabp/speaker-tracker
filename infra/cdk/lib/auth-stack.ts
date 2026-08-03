import { ArnFormat, CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface AuthStackProps extends StackProps {
  /** App origin, for OAuth callback/logout URLs (must match the SPA's redirect_uri). */
  readonly appUrl: string;
  /** Cognito hosted domain prefix, e.g. speakertracker-app-381492047863. */
  readonly cognitoDomainPrefix: string;
  /** SES sender for invitations and password resets. Passed in rather than imported so the stack
   *  stays testable without the real verified domain. */
  readonly authEmail: {
    readonly fromAddress: string;
    readonly fromName: string;
    /** Region holding the verified identity — not necessarily this stack's region. */
    readonly sesRegion: string;
    readonly sesVerifiedDomain: string;
  };
}

/**
 * Cognito for prod: invite-only user pool, a public SPA client (PKCE), and
 * Managed Login (Essentials plan + branding). Pool is RETAINed — it holds a
 * client's contacts and correspondence, so a stack teardown must not delete it.
 */
export class AuthStack extends Stack {
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);

    // Invite-only: no self-signup, admin-created users, RETAIN on teardown.
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'speaker-tracker',
      featurePlan: cognito.FeaturePlan.ESSENTIALS, // required for Managed Login branding
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      // SES, not Cognito's built-in mailer.
      //
      // COGNITO_DEFAULT sends from `no-reply@verificationemail.com`, caps at ~50/day, and lands in
      // spam often enough that Donna's invitation never arrived — which left her with no way in at
      // all, because Cognito refuses a password reset for a user who has never set one. Auth mail
      // is the one category with no in-app fallback: if it does not arrive, the user is locked out
      // and only an admin can help.
      email: cognito.UserPoolEmail.withSES({
        fromEmail: props.authEmail.fromAddress,
        fromName: props.authEmail.fromName,
        sesRegion: props.authEmail.sesRegion,
        sesVerifiedDomain: props.authEmail.sesVerifiedDomain,
      }),
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Public SPA client (browser, no client secret) — the auth-code exchange is secured by
    // PKCE, which Cognito requires for public clients. OIDC auth-code flow, 90-day refresh.
    this.userPoolClient = this.userPool.addClient('SpaClient', {
      userPoolClientName: 'speaker-tracker-spa',
      generateSecret: false,
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [props.appUrl],
        logoutUrls: [props.appUrl],
      },
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(90),
    });

    // Managed Login (not classic Hosted UI): needs the newer domain version + a branding
    // resource, else the sign-in page can render unstyled. useCognitoProvidedValues = default theme.
    this.userPool.addDomain('Domain', {
      cognitoDomain: { domainPrefix: props.cognitoDomainPrefix },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });
    new cognito.CfnManagedLoginBranding(this, 'ManagedLoginBranding', {
      userPoolId: this.userPool.userPoolId,
      clientId: this.userPoolClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    // Cognito logs nothing by default, which is why the failed invitation left no trace anywhere:
    // not in CloudTrail (hosted-UI events carry no error detail), not in our Lambdas (auth never
    // reaches them), not in the pool. ERROR-level notification logs record mail Cognito could not
    // deliver — the question that actually recurs, since every future password reset depends on it.
    //
    // `userAuthEvents`, which would log a failed password change, is deliberately NOT enabled: it
    // requires the PLUS feature plan — a standing per-user cost for a diagnostic used about once a
    // year, and one that cannot be turned on retroactively for an incident already past.
    const authLogs = new logs.LogGroup(this, 'UserPoolLogs', {
      retention: logs.RetentionDays.THREE_MONTHS,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    new cognito.CfnLogDeliveryConfiguration(this, 'UserPoolLogDelivery', {
      userPoolId: this.userPool.userPoolId,
      logConfigurations: [
        {
          eventSource: 'userNotification',
          logLevel: 'ERROR',
          // NOT `authLogs.logGroupArn` — that ends in `:*` (the wildcard form IAM wants for
          // "every stream in this group"), and Cognito validates the ARN against a pattern that
          // rejects it outright:
          //
          //   Value 'arn:…:log-group:…:*' at 'cloudWatchLogsConfiguration.logGroupArn'
          //   failed to satisfy constraint
          //
          // The deploy fails at CREATE and rolls the whole stack back, so this takes the SES email
          // change down with it. Composed here instead, which yields the bare group ARN.
          cloudWatchLogsConfiguration: {
            logGroupArn: this.formatArn({
              service: 'logs',
              resource: 'log-group',
              resourceName: authLogs.logGroupName,
              arnFormat: ArnFormat.COLON_RESOURCE_NAME,
            }),
          },
        },
      ],
    });

    // No post_confirmation/post_authentication trigger by design — the API owns users-row
    // creation via an idempotent upsert on the first authenticated request (DESIGN.md §7).
    // A Cognito trigger would be best-effort at best: 5s cap vs 2-6s cold RDS TLS, and it
    // never fires for AdminCreateUser (already-confirmed) users.

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
    new CfnOutput(this, 'ManagedLoginDomain', {
      value: `https://${props.cognitoDomainPrefix}.auth.${this.region}.amazoncognito.com`,
    });
  }
}