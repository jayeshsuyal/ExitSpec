#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/.." && pwd)"
python_command="${EXITSPEC_PYTHON:-python3}"
browser_report="$(mktemp "${TMPDIR:-/tmp}/exitspec-v0-3-browser.XXXXXX")"
trap 'rm -f -- "${browser_report}"' EXIT

cd "${repository_root}"

# This release gate owns the browser-test opt-in so clean local and CI runs
# exercise the same legacy and convergence browser coverage.
export EXITSPEC_BROWSER_E2E=1

if ! "${python_command}" -c 'import playwright.sync_api' >/dev/null 2>&1; then
  printf 'The v0.3 release gate requires the browser extra.\n' >&2
  printf "Install it with: %s -m pip install -e '.[dev,browser]'\n" "${python_command}" >&2
  exit 2
fi

if ! "${python_command}" -c \
  'from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    available = Path(playwright.chromium.executable_path).is_file()
raise SystemExit(0 if available else 1)'
then
  printf 'The v0.3 release gate requires an installed Playwright Chromium binary.\n' >&2
  printf 'Install it with: %s -m playwright install chromium\n' "${python_command}" >&2
  exit 2
fi

printf 'ExitSpec v0.3 mandatory Chromium convergence gate.\n'
"${python_command}" -m pytest \
  --strict-markers \
  --runxfail \
  --junitxml="${browser_report}" \
  tests/test_a7_convergence_browser.py

"${python_command}" -c \
  'import sys, xml.etree.ElementTree as ET; root=ET.parse(sys.argv[1]).getroot(); cases=list(root.iter("testcase")); skipped=sum(1 for case in cases if case.find("skipped") is not None); failed=sum(1 for case in cases if case.find("failure") is not None or case.find("error") is not None); expected=4; print(f"Mandatory Chromium cases: {len(cases)}; skipped: {skipped}; failed: {failed}"); raise SystemExit(0 if len(cases) == expected and skipped == 0 and failed == 0 else 1)' \
  "${browser_report}"

exec "${script_directory}/engineering_gate.sh"
