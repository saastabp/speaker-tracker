# Speaker Tracker — Production Architecture Diagram

Companion to `speaker-tracker-prod-architecture.drawio`.

Built on **2026-08-12** from live CloudFormation / service state in account `381492047863`, not from
the CDK source alone. Every resource identifier below was read back from the deployed account.

Open the `.drawio` file in [app.diagrams.net](https://app.diagrams.net), the VS Code *Draw.io
Integration* extension, or draw.io Desktop. Icons are the official AWS Architecture Icons shipped in
draw.io's built-in `mxgraph.aws4` stencil library.

## Pages

| Page | Purpose |
|---|---|
| **Production Architecture** | Every deployed prod component and how requests, data, and failures move between them |
| **Email Round-Trip** | Detail view of the outbound (SES) and inbound (IMAP poll) mail path |

## Stacks represented

Five CloudFormation stacks make up prod. Note the naming: the bare `Prod-*` / `Sandbox-*` stacks in
this account belong to a **sibling project** and are not part of Speaker Tracker.

| Stack | Region | What it owns |
|---|---|---|
| `speaker-tracker-prod-Cert` | us-east-1 | ACM certificate for the CloudFront alias |
| `speaker-tracker-prod-Auth` | us-west-2 | Cognito user pool, Managed Login branding, SPA client, log delivery |
| `speaker-tracker-prod-Messaging` | us-west-2 | The IMAP secret (created empty) |
| `speaker-tracker-prod-Api` | us-west-2 | HTTP API + 96 routes, 4 Lambdas, content bucket, scheduler group, DLQ, alarms, SNS |
| `speaker-tracker-prod-Frontend` | us-west-2 | SPA bucket, CloudFront distribution + 2 functions, Route 53 alias records |

## Request path

1. Browser resolves `speaker-tracker.360balancedliving.com` via **Route 53** alias A/AAAA records.
2. **CloudFront** (`E133RYDU8CSQYZ`) terminates TLS with the us-east-1 **ACM** certificate.
3. Default behavior serves the SPA from the **S3 SPA bucket** over Origin Access Control — the
   bucket has no public access. A CloudFront Function rewrites unknown paths to `index.html` so
   client-side routing works on refresh.
4. `/api/*` goes to the **API Gateway HTTP API** (`uoek3i94hk`). A second CloudFront Function strips
   the `/api` prefix before the origin request.
5. A **Cognito** JWT authorizer validates the token. Login itself happens against Cognito Managed
   Login directly from the browser, not through CloudFront.
6. The **api Lambda** (python3.12, 1024 MB, 15 s, reserved concurrency 5) handles every route.

## Data and storage

- **RDS MySQL `jobtracker-db`** — `db.t4g.micro`, single-AZ in `us-west-2b`. This instance is
  **shared with the sibling job-tracker app** and is not created by these stacks; its coordinates are
  read at deploy time from `/jobtracker/data/*` SSM parameters. Speaker Tracker uses the
  `speakertracker` schema.
- **No Lambda is VPC-attached.** The database is reached over its public endpoint with IAM
  authentication (`rds-db:connect`, scoped to the `DbiResourceId`). There is no NAT gateway anywhere
  in this app — a deliberate cost and complexity choice.
- **S3 content bucket** — materials and email attachments. The API mints presigned URLs; the browser
  PUTs and GETs directly against S3, so file bytes never pass through Lambda. CORS allows only
  `https://speaker-tracker.360balancedliving.com`.
- **migrate Lambda** runs schema migrations once per deploy, driven by a CDK `Custom::Trigger`.

## Scheduled and asynchronous work

**Follow-up reminders.** The API creates a one-shot **EventBridge Scheduler** schedule per reminder
in the `speaker-tracker-prod-followups` group. At its due time the schedule invokes the
**followup-notify Lambda**, which sends the reminder through SES. On failure the invocation lands in
the `speaker-tracker-prod-followup-failures` **SQS DLQ**; a **followup-failed Lambda** consumes it,
and a CloudWatch alarm fires on that consumer being invoked at all (`Invocations ≥ 1`).

**Inbound mail.** An **EventBridge rule** on `rate(1 minute)` invokes the **imap-poll Lambda**
(reserved concurrency 1) which reads credentials from **Secrets Manager**, fetches from the WorkMail
mailbox over IMAP, and writes threads and messages to RDS. An alarm fires on `Errors ≥ 1`.

Both alarms publish to the **same SNS topic**, `speaker-tracker-prod-ops-alerts`, which emails
`saastabp@gmail.com`. One topic on purpose: a confirmed email subscription is the only thing between
an alarm and a human, and every extra topic is another confirmation link someone has to remember to
click before it carries anything. The topic was called `speaker-tracker-prod-imap-poll-alarm` until
2026-08-12, a name it outgrew when the follow-up DLQ alarm joined it.

## Email

Outbound goes through **SES** in us-east-1, using the `360balancedliving.com` domain identity with
DKIM signing. The identity is **referenced, never created** by CDK — it is shared with other senders
on the domain, and a CDK-owned identity could delete their verification.

Inbound arrives at a **WorkMail mailbox in a different AWS account** (`730335513412`, Donna's
organization). Reaching it is a username/password IMAP problem rather than a cross-account IAM one.
The poller moves each message from `Speaker Tracker/Import` to `Speaker Tracker/Processed`, and that
folder move is what makes processing idempotent.

Two facts on page 2 are worth carrying in your head:

- **SES rewrites the `Message-ID`.** The header the recipient sees is not the one we mint, so the id
  returned by `SendRawEmail` is stored as `external_message_id`. That is what inbound `In-Reply-To` /
  `References` headers match against.
- **Exactly one environment may poll the mailbox.** Sandbox polling has been off since 2026-08-03.
  Two pollers on a one-minute schedule would race over the `Import` folder and split inbound mail
  across two databases at random.

## Reading the edges

| Style | Meaning |
|---|---|
| Solid black | Request or data path |
| Dashed black | Async, scheduled, configuration, or credential read |
| Dashed red | Failure path |

## Regenerating

The diagram is hand-authored XML, so it does not regenerate itself. When infrastructure changes,
re-read live state and edit the affected cells:

```bash
export AWS_PROFILE=brian-admin
aws cloudformation list-stack-resources --stack-name speaker-tracker-prod-Api \
  --query 'StackResourceSummaries[].[ResourceType,LogicalResourceId,PhysicalResourceId]' --output text
```

## Exporting

draw.io Desktop 31.1.8 is installed at `/opt/drawio/drawio` (on PATH as `drawio`). Both pages export
with:

```bash
cd /home/brians/360-balanced-living/speaker-tracker
PROFILE=$(mktemp -d)

drawio --user-data-dir="$PROFILE" --no-sandbox --disable-gpu \
  -x -f png -e -b 10 -p 1 --scale 2 \
  -o docs/diagrams/speaker-tracker-prod-architecture.drawio.png \
  docs/diagrams/speaker-tracker-prod-architecture.drawio

drawio --user-data-dir="$PROFILE" --no-sandbox --disable-gpu \
  -x -f png -e -b 10 -p 2 --scale 2 \
  -o docs/diagrams/speaker-tracker-email-round-trip.drawio.png \
  docs/diagrams/speaker-tracker-prod-architecture.drawio
```

Three flags are load-bearing:

- **`-p` is 1-indexed** as of draw.io v27.0.2 (it was 0-indexed before). Passing `-p 0` fails with
  `Invalid page index`.
- **`--user-data-dir`** avoids the Electron singleton lock in `~/.config/draw.io`. Without it, the
  *second* and later CLI invocations hang indefinitely instead of exporting — they try to attach to
  the profile a previous run left behind. This is the failure mode to remember; it looks like a hung
  export with no error output and no exit.
- **`-e`** embeds the source XML in the PNG, so the image stays re-editable if it is dragged back
  into draw.io.

`--disable-gpu` only silences MESA warnings; software rendering is used either way, and the
`MESA-LOADER: failed to open dri` line on stderr is harmless.
