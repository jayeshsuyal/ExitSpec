#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/.." && pwd)"
python_command="${EXITSPEC_PYTHON:-python3}"

cd "${repository_root}"

if ! "${python_command}" -c 'import playwright' >/dev/null 2>&1; then
  printf 'The v0.1 release gate requires the browser extra.\n' >&2
  printf "Install it with: %s -m pip install -e '.[dev,browser]'\n" "${python_command}" >&2
  exit 2
fi

printf 'ExitSpec v0.1 release gate: deterministic providers only.\n'
printf 'Chromium lifecycle coverage is mandatory for this gate.\n'

export EXITSPEC_BROWSER_E2E=1
exec "${script_directory}/engineering_gate.sh"
