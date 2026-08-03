// Build identity, injected by `vite.config.ts` and rendered at the foot of the sidebar.
//
// The point is a screenshot. When something looks wrong, the version and commit are visible in the
// same picture as the problem, so "which build is that?" never needs asking.

/** The `package.json` version this bundle was built from. */
export const APP_VERSION: string = __APP_VERSION__;

/** Short commit sha, suffixed `-dirty` if the tree had uncommitted changes, or `unknown`. */
export const BUILD_REF: string = __BUILD_REF__;

/**
 * The label shown in the sidebar — `v1.0.0 · 145272e`, plus the mode anywhere but production.
 *
 * Production is the only build that gets a bare label. Sandbox and dev append their mode because
 * the two are otherwise pixel-identical, and a sandbox screenshot mistaken for production sends
 * you debugging the wrong database.
 *
 * @param mode - Vite mode; defaults to the build's own. A parameter so this is testable.
 */
export function versionLabel(mode: string = import.meta.env.MODE): string {
  const base = `v${APP_VERSION} · ${BUILD_REF}`;
  return mode === 'production' ? base : `${base} · ${mode}`;
}