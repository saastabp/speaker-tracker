/**
 * Runtime configuration fetched at boot. In prod, the CDK Frontend stack writes `/config.json`
 * into the bucket from the prod-Auth stack's outputs, so the Cognito values have a single source
 * of truth (the deployed stack) and are never hand-copied into a committed file. Sandbox uses the
 * dev auth shim and needs no config.
 */
export interface RuntimeConfig {
  oidcAuthority: string;
  oidcClientId: string;
}

/** Load `/config.json` in cognito mode; return null in sandbox dev mode (no OIDC). */
export async function loadRuntimeConfig(): Promise<RuntimeConfig | null> {
  if (import.meta.env.VITE_AUTH_MODE !== 'cognito') {
    return null;
  }
  const res = await fetch('/config.json', { cache: 'no-store' });
  if (!res.ok) {
    throw new Error(`failed to load /config.json (${res.status})`);
  }
  return (await res.json()) as RuntimeConfig;
}