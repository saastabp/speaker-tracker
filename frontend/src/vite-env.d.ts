/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Selected at build time by the Vite mode. Mirrors the backend AUTH_MODE split. In cognito
   *  mode the Cognito authority/client id come from /config.json at runtime, not from env. */
  readonly VITE_AUTH_MODE: 'dev' | 'cognito';
  /** Optional local-dev proxy target for /api (see vite.config.ts). */
  readonly VITE_DEV_API_PROXY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}