/**
 * Unit tests for the two CloudFront Functions attached to the Frontend distribution.
 *
 * These used to be inline template literals inside `frontend-stack.ts`, which made them untestable,
 * invisible to `tsc`, and quietly dependent on double-escaping (`\\/api` in the literal had to emit
 * `/^\/api/`). Get that wrong and the regex silently stops matching: every API request forwards with
 * the prefix intact and 404s from API Gateway, with nothing failing at synth or deploy time.
 *
 * They now live as real `.js` files. Rather than re-declare the logic here, each test evaluates the
 * shipped file in a `vm` sandbox and pulls out `handler` — so these assertions run against the exact
 * bytes CDK uploads, and a bad edit to the source cannot pass by being absent from the test.
 */
import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as fs from 'fs';
import * as path from 'path';
import * as vm from 'vm';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { TEST_EMAIL_CONFIG, TEST_POLL_CONFIG } from './fixtures';

const ENV = { account: '111111111111', region: 'us-west-2' };

/** Read a CloudFront Function source file exactly as CDK's `fromFile` does. */
function readSource(name: string): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'lib', 'cloudfront-functions', `${name}.js`),
    'utf8',
  );
}

/** Load a CloudFront Function source file and return its global `handler`. */
function loadHandler(name: string): (event: { request: { uri: string } }) => { uri: string } {
  // The files declare a bare global `handler` (CloudFront requires it); trailing expression returns it.
  return vm.runInNewContext(`${readSource(name)}\n;handler`);
}

/** Run the handler the way CloudFront does and return the (possibly rewritten) URI. */
function rewrite(handler: ReturnType<typeof loadHandler>, uri: string): string {
  return handler({ request: { uri } }).uri;
}

describe('spa-fallback function', () => {
  const handler = loadHandler('spa-fallback');

  it.each([
    ['/', '/index.html'],
    ['/dashboard', '/index.html'],
    ['/opportunities/42', '/index.html'],
    ['/contacts/5/timeline', '/index.html'],
  ])('rewrites the extension-less path %s to the SPA entry point', (uri, expected) => {
    expect(rewrite(handler, uri)).toBe(expected);
  });

  it.each([
    '/index.html',
    '/assets/index-a1b2c3d4.js',
    '/assets/index-a1b2c3d4.css',
    '/favicon.ico',
    '/config.json',
    '/logo.svg',
  ])('leaves the asset path %s untouched', (uri) => {
    expect(rewrite(handler, uri)).toBe(uri);
  });

  it('passes /config.json through, since prod reads OIDC values from it at boot', () => {
    // Rewriting this to index.html would hand the SPA HTML where it expects JSON, and auth would
    // fail at startup with a parse error rather than anything that names the real cause.
    expect(rewrite(handler, '/config.json')).toBe('/config.json');
  });

  it('does NOT rewrite a route containing a dot — known limit of the heuristic', () => {
    // Documented, not desired. The dot test cannot distinguish a dotted route segment from a file
    // extension. Safe while routes are id-based; adding a route like this would 404 in production.
    expect(rewrite(handler, '/contacts/jane.doe')).toBe('/contacts/jane.doe');
  });
});

describe('api-strip function', () => {
  const handler = loadHandler('api-strip');

  it.each([
    ['/api/health', '/health'],
    ['/api/opportunities', '/opportunities'],
    ['/api/emails/threads/12', '/emails/threads/12'],
    ['/api/targets/venue/weekly', '/targets/venue/weekly'],
  ])('strips the /api prefix from %s', (uri, expected) => {
    expect(rewrite(handler, uri)).toBe(expected);
  });

  it.each([
    ['/api', '/'],
    ['/api/', '/'],
  ])('normalises %s to the root path', (uri, expected) => {
    // Stripping leaves "", which is not a valid URI for the origin.
    expect(rewrite(handler, uri)).toBe(expected);
  });

  it('strips only the leading occurrence', () => {
    expect(rewrite(handler, '/api/foo/api/bar')).toBe('/foo/api/bar');
  });

  it('requires a path separator after /api, so /apiary is left intact', () => {
    // Regression guard. A bare /^\/api/ matches the first four characters here and yields "ary" —
    // no leading slash, a malformed URI sent upstream. Unreachable while the behavior pattern is
    // `/api/*` (CloudFront requires the literal `/api/`, so this falls to the default behavior),
    // but loosening that pattern to `/api*` would make it live. The lookahead is what holds.
    expect(rewrite(handler, '/apiary')).toBe('/apiary');
  });

  it('always returns a URI beginning with a slash', () => {
    for (const uri of ['/api', '/api/', '/api/x', '/apiary', '/api/a/b/c']) {
      expect(rewrite(handler, uri)).toMatch(/^\//);
    }
  });
});

describe('the distribution ships these exact files', () => {
  // The tests above prove the logic is right; this proves the stack is pointing at it. Without this,
  // renaming a file or swapping the two `FUNCTION_SRC` arguments would leave every assertion above
  // green while deploying the wrong code — the SPA fallback stripping /api and vice versa.
  const template = (() => {
    const app = new App({ context: { 'aws:cdk:bundling-stacks': [] } });
    const api = new ApiStack(app, 'TestApi', {
      env: ENV,
      appName: 'speaker-tracker',
      envType: 'sandbox',
      authMode: 'dev',
      dbName: 'speakertracker_sandbox',
      logRetention: logs.RetentionDays.ONE_MONTH,
      reservedConcurrency: {},
      ...TEST_POLL_CONFIG,
      email: TEST_EMAIL_CONFIG,
      contentCorsOrigins: ['https://example.test'],
    });
    const frontend = new FrontendStack(app, 'TestFrontend', {
      env: ENV,
      envType: 'sandbox',
      httpApi: api.httpApi,
    });
    return Template.fromStack(frontend);
  })();

  /** The synthesized FunctionCode for the function whose logical id starts with `prefix`. */
  function emittedCode(prefix: string): string {
    const match = Object.entries(template.findResources('AWS::CloudFront::Function')).find(
      ([logicalId]) => logicalId.startsWith(prefix),
    );
    if (!match) throw new Error(`no CloudFront Function with logical id starting ${prefix}`);
    return match[1].Properties.FunctionCode;
  }

  it.each([
    ['SpaFallbackFn', 'spa-fallback'],
    ['ApiStripFn', 'api-strip'],
  ])('%s carries the contents of %s.js verbatim', (logicalId, fileName) => {
    expect(emittedCode(logicalId)).toBe(readSource(fileName));
  });

  it('does not cross the two sources', () => {
    expect(emittedCode('SpaFallbackFn')).toContain('/index.html');
    expect(emittedCode('SpaFallbackFn')).not.toContain("replace(/^\\/api");
    expect(emittedCode('ApiStripFn')).toContain("replace(/^\\/api");
    expect(emittedCode('ApiStripFn')).not.toContain("request.uri = '/index.html'");
  });

  it('emits both functions on the JS 2.0 runtime', () => {
    for (const fn of Object.values(template.findResources('AWS::CloudFront::Function'))) {
      expect(fn.Properties.FunctionConfig.Runtime).toBe('cloudfront-js-2.0');
    }
  });
});