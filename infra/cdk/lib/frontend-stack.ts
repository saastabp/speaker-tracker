import { CfnOutput, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';

/** Placeholder served until the Vite SPA build is wired in (frontend-shell step). */
const PLACEHOLDER_HTML = `<!doctype html><meta charset="utf-8">
<title>Speaker Tracker</title>
<body style="font-family:sans-serif;background:#FBF8F2;color:#1F3B4D;padding:3rem">
<h1>Speaker Tracker</h1><p>Sandbox shell placeholder — SPA build not yet deployed.</p>`;

/** Rewrite extension-less paths to /index.html so the SPA router owns them. Default behavior only. */
const SPA_FALLBACK_FN = `function handler(event) {
  var request = event.request;
  if (request.uri.includes('.')) { return request; }
  request.uri = '/index.html';
  return request;
}`;

/** Strip the /api prefix before the request reaches the HTTP API ($default stage, no stage prefix). */
const API_STRIP_FN = `function handler(event) {
  var request = event.request;
  request.uri = request.uri.replace(/^\\/api/, '');
  if (request.uri === '') { request.uri = '/'; }
  return request;
}`;

export interface FrontendStackProps extends StackProps {
  readonly envType: 'sandbox' | 'prod';
  /** API to serve under /api/* (same-origin, no CORS). */
  readonly httpApi: apigwv2.IHttpApi;
  /** Prod custom domain (cert is cross-region from us-east-1). Absent → sandbox default *.cloudfront.net. */
  readonly customDomain?: {
    readonly domainName: string;
    readonly certificate: acm.ICertificate;
    readonly hostedZoneId: string;
    readonly zoneName: string;
  };
  /** Cognito wiring (prod). When present, /config.json is written with the OIDC values so the
   *  SPA fetches them at runtime and never carries them at build time. */
  readonly auth?: {
    readonly userPool: cognito.IUserPool;
    readonly userPoolClient: cognito.IUserPoolClient;
  };
}

/**
 * S3 + CloudFront serving the SPA and proxying /api/* to the HTTP API from one
 * origin (same-origin, no CORS). Sandbox uses the default *.cloudfront.net
 * domain — no ACM cert, no Route53.
 */
export class FrontendStack extends Stack {
  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, 'SpaBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const spaFallback = new cloudfront.Function(this, 'SpaFallbackFn', {
      code: cloudfront.FunctionCode.fromInline(SPA_FALLBACK_FN),
      runtime: cloudfront.FunctionRuntime.JS_2_0,
    });
    const apiStrip = new cloudfront.Function(this, 'ApiStripFn', {
      code: cloudfront.FunctionCode.fromInline(API_STRIP_FN),
      runtime: cloudfront.FunctionRuntime.JS_2_0,
    });

    // API Gateway HTTP API origin: {apiId}.execute-api.{region}.amazonaws.com, HTTPS only.
    const apiOrigin = new origins.HttpOrigin(
      `${props.httpApi.apiId}.execute-api.${this.region}.amazonaws.com`,
      { protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY },
    );

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: `${props.envType}-speaker-tracker`,
      defaultRootObject: 'index.html',
      // Custom domain + cert in prod; sandbox serves from the default *.cloudfront.net.
      domainNames: props.customDomain ? [props.customDomain.domainName] : undefined,
      certificate: props.customDomain?.certificate,
      // SPA static assets from S3 via OAC; extension-less paths fall back to index.html.
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        functionAssociations: [
          { function: spaFallback, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST },
        ],
      },
      additionalBehaviors: {
        // Same-origin API. Forward Authorization + X-User-Timezone, suppress Host so
        // API Gateway routes on its own hostname; caching disabled (required to forward auth).
        '/api/*': {
          origin: apiOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          functionAssociations: [
            { function: apiStrip, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST },
          ],
        },
      },
    });

    // Placeholder SPA content, plus (prod) the runtime OIDC config the SPA fetches at boot — its
    // values come from the prod-Auth stack, so they are never hand-copied into a committed file.
    // Invalidate only these paths; real Vite assets are hashed/immutable.
    // TODO(frontend-shell): replace Source.data with Source.asset(frontend/dist build).
    const sources = [s3deploy.Source.data('index.html', PLACEHOLDER_HTML)];
    const distributionPaths = ['/index.html'];
    if (props.auth) {
      sources.push(
        s3deploy.Source.jsonData('config.json', {
          oidcAuthority: `https://cognito-idp.${this.region}.amazonaws.com/${props.auth.userPool.userPoolId}`,
          oidcClientId: props.auth.userPoolClient.userPoolClientId,
        }),
      );
      distributionPaths.push('/config.json');
    }
    new s3deploy.BucketDeployment(this, 'SpaContent', {
      sources,
      destinationBucket: bucket,
      distribution,
      distributionPaths,
    });

    // Prod: alias the custom domain (A + AAAA) at the distribution. Same-account zone.
    if (props.customDomain) {
      const zone = route53.HostedZone.fromHostedZoneAttributes(this, 'Zone', {
        hostedZoneId: props.customDomain.hostedZoneId,
        zoneName: props.customDomain.zoneName,
      });
      const target = route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(distribution));
      new route53.ARecord(this, 'AliasA', { zone, recordName: props.customDomain.domainName, target });
      new route53.AaaaRecord(this, 'AliasAaaa', { zone, recordName: props.customDomain.domainName, target });
    }

    new CfnOutput(this, 'DistributionUrl', {
      value: `https://${props.customDomain?.domainName ?? distribution.distributionDomainName}`,
      description: 'URL serving the SPA and /api/* (prod: custom domain; sandbox: *.cloudfront.net).',
    });
  }
}