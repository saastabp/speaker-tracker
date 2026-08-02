import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { RETURN_TO_KEY, useAuthSession } from './session';

/**
 * Narrow a stored deep link to a same-origin path, or null.
 *
 * `signIn` only ever stores `window.location.pathname + search`, so the app never writes a foreign
 * URL here — but sessionStorage is not a trust boundary, and the browser's own `pathname` is
 * attacker-influenced: a link to `https://app/\/evil.com` yields a pathname the router resolves as
 * **protocol-relative**, sending the user to another origin immediately after they authenticate.
 * For a CRM that is a ready-made phishing flow, so the value is checked at the point of use rather
 * than trusted because of where it came from.
 */
export function safeReturnTo(value: string | null): string | null {
  if (!value || !value.startsWith('/')) return null;
  // `//host` and `/\host` (and the backslash variants the router normalises) leave the origin.
  if (value.startsWith('//') || value.startsWith('/\\')) return null;
  return value;
}

/**
 * After sign-in, navigate to the deep link the user originally requested. `signIn` stores the
 * intended path before redirecting to Cognito; once authenticated, we route to it via the router
 * (a raw history.replaceState in onSigninCallback would change the URL but not the rendered
 * route). Mode-agnostic: dev mode never stores a path, so this is a no-op there.
 */
export function DeepLinkRestorer() {
  const { isAuthenticated } = useAuthSession();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAuthenticated) {
      return;
    }
    const stored = sessionStorage.getItem(RETURN_TO_KEY);
    if (!stored) {
      return;
    }
    // Cleared whether or not it survives validation, so a rejected value cannot linger and fire on
    // the next sign-in.
    sessionStorage.removeItem(RETURN_TO_KEY);
    const returnTo = safeReturnTo(stored);
    if (returnTo) {
      navigate(returnTo, { replace: true });
    } else {
      // Dropping a deep link silently would hide the one case worth seeing (silent-fallback rule).
      console.warn('Ignored an off-origin return path after sign-in: %o', stored);
    }
  }, [isAuthenticated, navigate]);

  return null;
}