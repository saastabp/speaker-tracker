import type { ReactNode } from 'react';
import { AuthSessionContext, type AuthSession } from './session';

/**
 * Sandbox auth: a fixed dev principal with no token. The gateway is open (AUTH_MODE=dev), so
 * useApi sends no Authorization header. Mirrors common/auth.py's DEV_USER principal; the dev
 * user's `users` row is created lazily by the first authenticated request.
 */
const DEV_SESSION: AuthSession = {
  isAuthenticated: true,
  isLoading: false,
  user: { email: 'dev@speaker-tracker.local', name: null },
  idToken: null,
  signIn: () => {},
  signOut: () => {},
};

export function DevSession({ children }: { children: ReactNode }) {
  return <AuthSessionContext.Provider value={DEV_SESSION}>{children}</AuthSessionContext.Provider>;
}