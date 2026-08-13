// CloudFront Function (runtime JS 2.0) — viewer-request on the /api/* behavior (HTTP API origin).
//
// Strips the leading /api so /api/opportunities reaches API Gateway as /opportunities. The two ends
// disagree about the prefix on purpose: CloudFront needs /api/ in the path to tell API traffic from
// SPA traffic, while the HTTP API registers bare routes (GET /opportunities) on the $default stage,
// which contributes no stage prefix either. Without this, every request misses its route.
//
// The lookahead matters. A bare /^\/api/ also matches the first four characters of /apiary and
// yields "ary" — no leading slash, a malformed URI. That is unreachable while the behavior pattern
// is /api/* (which requires the literal /api/, so /apiary falls through to the default behavior),
// but it becomes live the moment someone writes /api* instead. Requiring a following slash or
// end-of-string costs nothing and removes the trap.
//
// The empty-string guard covers a request to exactly /api, which the replace reduces to "".
//
// Not a module. CloudFront Functions require a bare global `handler` — an `export` or
// `module.exports` here breaks deployment. test/cloudfront-functions.test.ts evaluates this file
// in a vm sandbox so the tests exercise the exact bytes that ship.

function handler(event) {
  var request = event.request;
  request.uri = request.uri.replace(/^\/api(?=\/|$)/, '');
  if (request.uri === '') {
    request.uri = '/';
  }
  return request;
}