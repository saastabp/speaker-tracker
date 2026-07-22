import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as route53 from 'aws-cdk-lib/aws-route53';
import { Construct } from 'constructs';

export interface CertStackProps extends StackProps {
  /** FQDN the cert secures, e.g. speaker-tracker.360balancedliving.com. */
  readonly domainName: string;
  /** Existing hosted zone for the parent domain. */
  readonly hostedZoneId: string;
  readonly zoneName: string;
}

/**
 * ACM certificate for the SPA domain. Lives in us-east-1 because CloudFront only
 * accepts certs from there; the prod Frontend (us-west-2) consumes it via a
 * cross-region reference. DNS-validated against the same-account hosted zone, so
 * validation needs no cross-account delegation.
 */
export class CertStack extends Stack {
  readonly certificate: acm.ICertificate;

  constructor(scope: Construct, id: string, props: CertStackProps) {
    super(scope, id, props);

    // fromHostedZoneAttributes, not fromLookup — no context cache, so synth needs no creds.
    const zone = route53.HostedZone.fromHostedZoneAttributes(this, 'Zone', {
      hostedZoneId: props.hostedZoneId,
      zoneName: props.zoneName,
    });

    // acm.Certificate + CertificateValidation.fromDns (DnsValidatedCertificate is deprecated).
    this.certificate = new acm.Certificate(this, 'Certificate', {
      domainName: props.domainName,
      validation: acm.CertificateValidation.fromDns(zone),
    });

    new CfnOutput(this, 'CertificateArn', { value: this.certificate.certificateArn });
  }
}