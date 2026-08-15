# Zoom golden-fixture operator kit

This dev-only tool acquires the first private, synthetic Zoom RTMS fixture
described in [`docs/ZOOM_GOLDEN_FIXTURE_RUNBOOK.md`](../../docs/ZOOM_GOLDEN_FIXTURE_RUNBOOK.md).
It is deliberately outside `src/exitspec`, excluded from the Python wheel, and
never used by `/app`.

Its authority is narrow:

> receive and preserve bounded opaque Zoom observations for one approved,
> transcript-only, two-person synthetic capture.

It does **not** define a Zoom packet schema, map packets into ExitSpec, create a
source, confirm or freeze a contract, measure a POC, assign a verdict, publish
a fixture, or authorize production use.

## Hard stop

Do not launch the live harness until every prerequisite in the runbook is
truthfully satisfied. In particular:

- Zoom Developer Pack credits are active;
- the General App is user-managed and non-production;
- only `meeting:read:meeting_transcript` and
  `meeting:update:participant_rtms_app_status` are granted;
- only `meeting.rtms_started`, `meeting.rtms_stopped`, and
  `meeting.rtms_interrupted` are subscribed;
- a reviewed HTTPS tunnel forwards only to the public callback port;
- exactly two synthetic participants accepted the fixed disclosure; and
- `python -m exitspec.zoom_fixture_capture preflight` created the exact private
  workspace.

The harness refuses arbitrary evidence directories. `CAPTURE_RAW_DIR` must be
the existing owner-only path
`.zoom-fixture-private/<capture-id>/raw`, with compatible plan and preflight
control files directly above it. At startup it also invokes ExitSpec's
canonical `verify-preflight` command in a secret-free child environment; a
hand-written, mutated, or expired receipt cannot satisfy that gate.

## Install and test

Node.js 20.12 or newer is required.

```bash
npm ci --prefix tools/zoom_fixture_operator
npm test --prefix tools/zoom_fixture_operator
npm audit --prefix tools/zoom_fixture_operator --audit-level=high
```

The tests use only synthetic local values. They make no Zoom request and write
no repository evidence.

## Prepare the private capture

Follow the runbook to create a truthful operator plan, then run:

```bash
python -m exitspec.zoom_fixture_capture preflight \
  --plan /absolute/path/to/operator-plan.json \
  --repository-root .
```

Copy the environment template to the ignored local filename and restrict it to
the operator:

```bash
cp tools/zoom_fixture_operator/.env.operator.example \
  tools/zoom_fixture_operator/.env.operator
chmod 600 tools/zoom_fixture_operator/.env.operator
```

Enter the three Zoom app values locally. Never place them in chat, a command
argument, Git, an issue, a screenshot, or a log. Use fresh unguessable callback
paths. Keep all three live gates `false` until the plan, credits, consent, and
schedule are true.

## Run once

The first real capture must use one fresh capture ID and one uninterrupted
harness process:

```bash
node --env-file=tools/zoom_fixture_operator/.env.operator \
  tools/zoom_fixture_operator/capture-harness.mjs
```

The harness exposes two loopback listeners:

- the public callback receiver on `PUBLIC_PORT`, which the reviewed HTTPS
  tunnel may forward; and
- the operator console on `OPERATOR_PORT`, which must remain loopback-only.

The local operator console shows the exact webhook and OAuth callback URLs.
Use those values in the Zoom app, complete URL validation, authorize OAuth in
the same process, record the two-person consent gate, and start RTMS only after
the invited participant has joined. The media handshake requests transcript
only (`media_type: 8`).

The process holds OAuth tokens in memory, never logs transcript content or
provider identifiers, injects one bounded post-transcript media disconnect,
captures one exact duplicate replay, and automatically attempts to stop RTMS
after the configured capture window.

## Seal and review

After RTMS has stopped, terminate the harness and follow the runbook exactly:

```bash
python -m exitspec.zoom_fixture_capture seal \
  --capture-id <capture-id> --repository-root .
python -m exitspec.zoom_fixture_capture verify \
  --capture-id <capture-id> --repository-root .
```

The original remains private. Do not commit any `.zoom-fixture-private`
content. A separate privacy review decides whether a sanitized candidate may
even be prepared; it does not authorize publication or a mapper.

## Upstream basis

The WebSocket flow was adapted from Zoom's MIT-licensed `rtms-samples` at the
pinned commit recorded in [`NOTICE.md`](NOTICE.md). The operator kit adds
ExitSpec-specific custody, consent, bounded retention, replay, and
fail-closed authority gates.
