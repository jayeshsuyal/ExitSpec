# Contributing to ExitSpec

ExitSpec welcomes focused changes that preserve its central rule: missing,
invalid, or insufficient evidence never passes.

## Development setup

ExitSpec requires Python 3.12 or newer, SQLite 3.37 or newer, and Node.js.

```bash
python3 -m pip install -e '.[dev]'
./scripts/engineering_gate.sh
```

Changes to browser behavior should also install and run the browser release
dependencies:

```bash
python3 -m pip install -e '.[dev,browser]'
python3 -m playwright install chromium
./scripts/v0_1_release_gate.sh
```

## Pull requests

- Keep one bounded decision per pull request.
- Complete the repository pull-request template, including scope, authority,
  failure behavior, evidence, limits, and rollback.
- Add regression tests for changed behavior and adversarial tests for changed
  authority or evidence boundaries.
- Update user-facing and architecture documentation in the same pull request
  when a capability or limitation changes.
- Do not rewrite frozen contracts or published evidence to make a new
  implementation look complete; add a separate implementation-evidence record.

The full merge standard is defined in the
[Engineering Playbook](docs/ENGINEERING_PLAYBOOK.md).

## Data and secrets

Use only approved synthetic fixtures. Never commit API keys, customer source,
real email, meeting audio, provider response bodies, or generated run artifacts.
Optional provider paths must remain disabled by default and fail closed when
configuration is missing.

## Security reports

Do not disclose a suspected vulnerability in a public issue. Follow the private
reporting instructions in [SECURITY.md](SECURITY.md).
