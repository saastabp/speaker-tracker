import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { RETURN_TO_KEY, useAuthSession } from './session';

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
    const returnTo = sessionStorage.getItem(RETURN_TO_KEY);
    if (returnTo) {
      sessionStorage.removeItem(RETURN_TO_KEY);
      navigate(returnTo, { replace: true });
    }
  }, [isAuthenticated, navigate]);

  return null;
}