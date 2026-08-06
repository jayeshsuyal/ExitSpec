# Security policy

ExitSpec v0.1 is a local synthetic demonstration, not a production security
boundary. Its current posture, threat coverage, and production gates are
documented in [Security and Privacy](docs/SECURITY.md).

## Reporting a vulnerability

Do not open a public issue containing vulnerability details, credentials,
customer data, raw audio, or provider payloads.

Use GitHub's private **Report a vulnerability** flow from this repository's
**Security** tab. If that flow is unavailable, contact the repository owner
through their GitHub profile to establish a private channel before sharing any
technical detail.

Include the affected revision, a minimal synthetic reproduction, impact, and
whether any secret or real-customer data may have been exposed. Do not attach
real customer inputs.

## Supported versions

Security fixes are applied to the current `0.1.x` line. The project makes no
support or production-readiness claim for untagged historical revisions.
