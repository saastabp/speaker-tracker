import type { ReactNode } from 'react';
import { DevSession } from './DevSession';
import { OidcSession } from './OidcSession';
import type { RuntimeConfig } from './runtimeConfig';

/**
 * Selects the auth implementation from the build-time VITE_AUTH_MODE: sandbox builds use the dev
 * shim (no Cognito), prod builds use real OIDC configured from the runtime config. Mirrors the
 * backend's AUTH_MODE split, so the same components run in both environments.
 */
export function AuthProvider({
  runtimeConfig,
  children,
}: {
  runtimeConfig: RuntimeConfig | null;
  children: ReactNode;
}) {
  if (import.meta.env.VITE_AUTH_MODE === 'dev') {
    return <DevSession>{children}</DevSession>;
  }
  if (!runtimeConfig) {
    throw new Error('runtime config (/config.json) is required in cognito auth mode');
  }
  return <OidcSession config={runtimeConfig}>{children}</OidcSession>;
}