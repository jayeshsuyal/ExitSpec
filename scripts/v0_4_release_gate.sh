#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_command="${EXITSPEC_PYTHON:-python3}"
browser_report="$(mktemp "${TMPDIR:-/tmp}/exitspec-v0-4-browser.XXXXXX")"
adversarial_report="$(mktemp "${TMPDIR:-/tmp}/exitspec-v0-4-adversarial.XXXXXX")"
artifact_reader_report="$(mktemp "${TMPDIR:-/tmp}/exitspec-v0-4-artifact-reader.XXXXXX")"
trap 'rm -f -- "${browser_report}" "${adversarial_report}" "${artifact_reader_report}"' EXIT

# The v0.3 wrapper owns the complete historical four-case Chromium and
# engineering gate. Keep it intact, then add the exact B13 collections below.
export EXITSPEC_BROWSER_E2E=1
"${script_directory}/v0_3_release_gate.sh"

if ! "${python_command}" -c 'import playwright.sync_api' >/dev/null 2>&1; then
  printf 'The v0.4 release gate requires the browser extra.\n' >&2
  exit 2
fi

if ! "${python_command}" -c \
  'from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    available = Path(playwright.chromium.executable_path).is_file()
raise SystemExit(0 if available else 1)'
then
  printf 'The v0.4 release gate requires an installed Playwright Chromium binary.\n' >&2
  printf 'Install it with: %s -m playwright install chromium\n' "${python_command}" >&2
  exit 2
fi

printf 'ExitSpec v0.4 mandatory B13 Chromium and adversarial gate.\n'
"${python_command}" -m pytest \
  --strict-markers \
  --runxfail \
  --junitxml="${browser_report}" \
  tests/test_b13_routing_evidence_pack_browser.py

"${python_command}" -c \
  'import sys, xml.etree.ElementTree as ET; root=ET.parse(sys.argv[1]).getroot(); cases=list(root.iter("testcase")); skipped=sum(1 for case in cases if case.find("skipped") is not None); failed=sum(1 for case in cases if case.find("failure") is not None or case.find("error") is not None); expected=4; print(f"B13 Chromium cases: {len(cases)}; skipped: {skipped}; failed: {failed}"); raise SystemExit(0 if len(cases) == expected and skipped == 0 and failed == 0 else 1)' \
  "${browser_report}"

"${python_command}" -m pytest \
  --strict-markers \
  --runxfail \
  --junitxml="${adversarial_report}" \
  tests/test_routing_evidence_pack.py

"${python_command}" -c \
  'import sys, xml.etree.ElementTree as ET; root=ET.parse(sys.argv[1]).getroot(); cases=list(root.iter("testcase")); skipped=sum(1 for case in cases if case.find("skipped") is not None); failed=sum(1 for case in cases if case.find("failure") is not None or case.find("error") is not None); expected=17; print(f"B13 adversarial cases: {len(cases)}; skipped: {skipped}; failed: {failed}"); raise SystemExit(0 if len(cases) == expected and skipped == 0 and failed == 0 else 1)' \
  "${adversarial_report}"

"${python_command}" -m pytest \
  --strict-markers \
  --runxfail \
  --junitxml="${artifact_reader_report}" \
  tests/test_routing_evidence_pack_artifact_reader.py

"${python_command}" -c \
  'import sys, xml.etree.ElementTree as ET; root=ET.parse(sys.argv[1]).getroot(); cases=list(root.iter("testcase")); skipped=sum(1 for case in cases if case.find("skipped") is not None); failed=sum(1 for case in cases if case.find("failure") is not None or case.find("error") is not None); expected=4; print(f"B13 direct artifact-reader cases: {len(cases)}; skipped: {skipped}; failed: {failed}"); raise SystemExit(0 if len(cases) == expected and skipped == 0 and failed == 0 else 1)' \
  "${artifact_reader_report}"

printf 'ExitSpec v0.4 B13 gate passed.\n'
