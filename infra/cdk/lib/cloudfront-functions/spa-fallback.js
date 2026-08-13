// CloudFront Function (runtime JS 2.0) — viewer-request on the DEFAULT behavior (S3 origin).
//
// Rewrites extension-less paths to /index.html so the SPA router owns them. The bucket holds only
// index.html plus content-hashed Vite assets, so a deep link or hard refresh on /opportunities/42
// is a 404 straight from S3 — that key does not exist. Rewriting hands the path to React Router,
// which does own it.
//
// The test is "does the path contain a dot": assets carry extensions, routes do not. That is a
// heuristic, not a rule — a route segment containing a literal dot (/contacts/jane.doe) is left
// alone and 404s. Current routes are id-based, so it does not bite; see the test file.
//
// Not a module. CloudFront Functions require a bare global `handler` — an `export` or
// `module.exports` here breaks deployment. test/cloudfront-functions.test.ts evaluates this file
// in a vm sandbox so the tests exercise the exact bytes that ship.

function handler(event) {
  var request = event.request;
  if (request.uri.includes('.')) {
    return request;
  }
  request.uri = '/index.html';
  return request;
}