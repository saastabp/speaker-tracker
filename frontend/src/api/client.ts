import { useCallback } from 'react';
import { useAuthSession } from '../auth/session';

/** A non-2xx (or network) API response, carrying the parsed error body when available. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export type ApiFetch = <T>(path: string, init?: RequestInit) => Promise<T>;

/** Browser IANA timezone (e.g. "Pacific/Honolulu"), sent so the API evaluates CURDATE() in the
 *  caller's local day — Kauaʻi is UTC-10, so date-bucketed metrics depend on it (ARCHITECTURE §1). */
function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/**
 * Same-origin API client: `fetch('/api'+path)` with the ID token + timezone header. A 401 is
 * treated as an auth event — it triggers sign-in rather than surfacing a raw Response to callers
 * (the bug ARCHITECTURE §1.1 explicitly avoids). Every other non-2xx becomes an {@link ApiError}.
 */
export function useApi(): ApiFetch {
  const { idToken, signIn } = useAuthSession();
  return useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      const headers = new Headers(init?.headers);
      headers.set('X-User-Timezone', browserTimezone());
      if (idToken) {
        headers.set('Authorization', `Bearer ${idToken}`);
      }
      // Default JSON only for string bodies — a FormData/Blob body sets its own Content-Type.
      if (typeof init?.body === 'string' && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
      }

      const res = await fetch(`/api${path}`, { ...init, headers });

      if (res.status === 401) {
        signIn();
        throw new ApiError(401, 'unauthorized');
      }

      if (!res.ok) {
        const isJson = res.headers.get('content-type')?.includes('application/json') ?? false;
        const errBody: unknown = isJson ? await res.json() : await res.text();
        const message =
          isJson && errBody && typeof errBody === 'object' && 'error' in errBody
            ? String((errBody as { error: unknown }).error)
            : `request failed (${res.status})`;
        throw new ApiError(res.status, message, errBody);
      }

      // Success bodies are always JSON per the API contract (bare-JSON envelope, ARCHITECTURE §2).
      return (await res.json()) as T;
    },
    [idToken, signIn],
  );
}