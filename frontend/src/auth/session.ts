import { createContext, useContext } from 'react';

/** sessionStorage key holding the path to restore after a sign-in redirect. */
export const RETURN_TO_KEY = 'speaker-tracker:returnTo';

/**
 * The auth state components consume, independent of whether the app is running against real
 * Cognito (prod) or the sandbox dev shim. Keeping components on this interface — never on
 * react-oidc-context directly — is what lets one codebase serve both environments.
 */
export interface AuthSession {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: { email: string } | null;
  /** Cognito ID token for the Authorization header, or null in sandbox dev mode. */
  idToken: string | null;
  /** Begin sign-in (redirect to Cognito in prod; no-op in sandbox). */
  signIn: () => void;
  /** End the local session. */
  signOut: () => void;
}

export const AuthSessionContext = createContext<AuthSession | null>(null);

export function useAuthSession(): AuthSession {
  const ctx = useContext(AuthSessionContext);
  if (!ctx) {
    throw new Error('useAuthSession must be used within an AuthProvider');
  }
  return ctx;
}