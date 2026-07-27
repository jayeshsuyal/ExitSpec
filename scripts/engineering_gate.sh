#!/usr/bin/env bash

set -euo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_directory}/.." && pwd)"
python_command="${EXITSPEC_PYTHON:-python3}"

cd "${repository_root}"

run_gate() {
  local label="$1"
  shift

  printf '\n==> %s\n' "${label}"
  "$@"
}

if ! command -v "${python_command}" >/dev/null 2>&1; then
  printf 'Required Python command not found: %s\n' "${python_command}" >&2
  exit 127
fi

if ! "${python_command}" -c \
  'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'
then
  printf 'ExitSpec requires Python 3.12 or newer: %s\n' "${python_command}" >&2
  printf 'Set EXITSPEC_PYTHON to a supported interpreter and retry.\n' >&2
  exit 2
fi

if ! command -v node >/dev/null 2>&1; then
  printf 'Required command not found: node\n' >&2
  exit 127
fi

if ! command -v git >/dev/null 2>&1; then
  printf 'Required command not found: git\n' >&2
  exit 127
fi

current_revision="$(git rev-parse --verify HEAD 2>/dev/null || true)"
if [[ -z "${current_revision}" ]]; then
  printf 'Engineering gate must run inside a Git worktree with a revision.\n' >&2
  exit 2
fi

printf 'ExitSpec engineering gate revision: %s\n' "${current_revision}"

untracked_files="$(git ls-files --others --exclude-standard)"
if [[ -n "${untracked_files}" ]]; then
  printf 'Untracked files are outside the proposed-change checks:\n' >&2
  printf '%s\n' "${untracked_files}" >&2
  printf 'Stage, commit, or intentionally ignore them before running the complete gate.\n' >&2
  exit 2
fi

run_gate \
  "Unstaged patch whitespace and conflict-marker check" \
  git diff --check

run_gate \
  "Staged patch whitespace and conflict-marker check" \
  git diff --cached --check

diff_base="${EXITSPEC_DIFF_BASE:-}"
if [[ -z "${diff_base}" ]]; then
  remote_default_ref="$(
    git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true
  )"
  if [[ -n "${remote_default_ref}" ]]; then
    diff_base="$(git merge-base HEAD "${remote_default_ref}")"
  elif git show-ref --verify --quiet refs/remotes/origin/main; then
    diff_base="$(git merge-base HEAD refs/remotes/origin/main)"
  elif git show-ref --verify --quiet refs/heads/main; then
    diff_base="$(git merge-base HEAD refs/heads/main)"
  else
    upstream_ref="$(
      git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' \
        2>/dev/null || true
    )"
    if [[ -n "${upstream_ref}" ]]; then
      diff_base="$(git merge-base HEAD "${upstream_ref}")"
    fi
  fi
fi

if [[ -n "${diff_base}" ]]; then
  if ! git rev-parse --verify "${diff_base}^{commit}" >/dev/null 2>&1; then
    printf 'Configured diff base is not available: %s\n' "${diff_base}" >&2
    exit 2
  fi

  printf 'ExitSpec engineering gate diff base: %s\n' "${diff_base}"
  run_gate \
    "Branch change whitespace and conflict-marker check" \
    git diff --check "${diff_base}...HEAD"
else
  printf 'No branch diff base was available; staged and unstaged checks still apply.\n'
fi

run_gate \
  "Built-wheel distribution proof" \
  "${python_command}" -m pytest tests/test_distribution.py

run_gate \
  "Python behavior suite" \
  "${python_command}" -m pytest --ignore=tests/test_distribution.py

run_gate \
  "Employee workbench JavaScript syntax" \
  node --check src/exitspec/static/app.js

run_gate \
  "Customer review JavaScript syntax" \
  node --check src/exitspec/static/review.js

printf '\nExitSpec engineering gate passed.\n'
