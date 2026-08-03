import { describe, expect, it } from 'vitest';
import { APP_VERSION, BUILD_REF, versionLabel } from './version';

describe('versionLabel', () => {
  it('is bare in production', () => {
    expect(versionLabel('production')).toBe(`v${APP_VERSION} · ${BUILD_REF}`);
  });

  it('names any other mode, so a sandbox screenshot cannot pass for production', () => {
    // The two builds are pixel-identical otherwise, and mistaking one for the other sends you
    // debugging the wrong database.
    expect(versionLabel('sandbox')).toBe(`v${APP_VERSION} · ${BUILD_REF} · sandbox`);
    expect(versionLabel('development')).toContain('development');
  });

  it('carries a version and a build ref, whatever the mode', () => {
    // Guards the injection itself: an undefined define would render "vundefined" and look like a
    // cosmetic glitch rather than the loss of the only link from a screenshot back to a commit.
    expect(APP_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
    expect(BUILD_REF).not.toBe('');
    expect(versionLabel('production')).not.toContain('undefined');
  });
});