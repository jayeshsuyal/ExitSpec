# Third-party notice

The Zoom RTMS signaling and transcript connection flow in this operator tool
was adapted from:

- Project: `zoom/rtms-samples`
- Repository: <https://github.com/zoom/rtms-samples>
- Pinned commit: `5c39fca2ed97d75bcbdb318cf246a037835f7d37`
- Copyright: 2026 Zoom Video Communications, Inc.
- License: MIT

The complete upstream license text is preserved in
[`ZOOM_RTMS_SAMPLES_LICENSE.md`](ZOOM_RTMS_SAMPLES_LICENSE.md).

ExitSpec-specific changes include transcript-only capture, local consent and
network gates, exact raw-body webhook verification, bounded opaque artifact
custody, duplicate and reconnect observations, process-memory OAuth handling,
and explicit denial of mapper, verdict, publication, and production authority.
