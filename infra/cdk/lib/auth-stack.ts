import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

export interface AuthStackProps extends StackProps {
  /** App origin, for OAuth callback/logout URLs (must match the SPA's redirect_uri). */
  readonly appUrl: string;
  /** Cognito hosted domain prefix, e.g. speakertracker-app-381492047863. */
  readonly cognitoDomainPrefix: string;
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