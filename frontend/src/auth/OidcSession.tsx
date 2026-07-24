import { WebStorageStateStore } from 'oidc-client-ts';
import { useMemo, type ReactNode } from 'react';
import {
  AuthProvider as OidcProvider,
  useAuth,
  type AuthProviderProps,
} from 'react-oidc-context';
import type { RuntimeConfig } from './runtimeConfig';
import { AuthSessionContext, RETURN_TO_KEY, type AuthSession } from './session';

/** Adapts react-oidc-context's useAuth() to our AuthSession, exposing the ID token (not the
 *  access token — API Gateway's JWT authorizer validates `aud`, which only the ID token carries). */
function OidcSessionBridge({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const session: AuthSession = useMemo(
    () => ({
      isAuthenticated: auth.isAuthenticated,
      isLoading: auth.isLoading,
      user: auth.user
        ? {
            email: auth.user.profile.email ?? '',
            name: auth.user.profile.given_name ?? auth.user.profile.name ?? null,
          }
        : null,
      idToken: auth.user?.id_token ?? null,
      signIn: () => {
        // Stash the deep link for DeepLinkRestorer to route to after the redirect returns.
        sessionStorage.setItem(RETURN_TO_KEY, window.location.pathname + window.location.search);
        // Don't let a failed redirect vanish silently (silent-fallback rule): surface it.
        auth.signinRedirect().catch((err) => console.error('signinRedirect failed', err));
      },
      // Local sign-out only (clears tokens), not a Cognito global logout — intended, so Donna
      // signs back in without re-entering the Cognito session (review decision).
      signOut: () => {
        auth.removeUser().catch((err) => console.error('removeUser failed', err));
      },
    }),
    [auth],
  );

  return <AuthSessionContext.Provider value={session}>{children}</AuthSessionContext.Provider>;
}

export function OidcSession({ config, children }: { config: RuntimeConfig; children: ReactNode }) {
  const oidcConfig: AuthProviderProps = {
    authority: config.oidcAuthority,
    client_id: config.oidcClientId,
    redirect_uri: window.location.origin,
    response_type: 'code',
    scope: 'openid email profile',
    automaticSilentRenew: true,
    // Tokens in localStorage so a browser restart stays signed in (DESIGN §1.2). Trade-off:
    // localStorage is readable by any XSS. Accepted for a single fixed-desktop user who signs in
    // ~quarterly; revisit for multi-user or mobile.
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    // Strip the ?code&state from the URL after processing; DeepLinkRestorer handles navigation.
    onSigninCallback: () => {
      window.history.replaceState({}, document.title, window.location.pathname);
    },
  };

  return (
    <OidcProvider {...oidcConfig}>
      <OidcSessionBridge>{children}</OidcSessionBridge>
    </OidcProvider>
  );
}