#!/usr/bin/env bash
# MacPatch: the changes ASCII Ink Visualizer needs on macOS. What each one does: macpatch/MACPATCH.md.
#
#   macpatch/apply.sh            apply the patch
#   macpatch/apply.sh --status   applied / applies cleanly / does not apply
#   macpatch/apply.sh --revert   take it back out
set -euo pipefail

cd "$(dirname "$0")/.."
PATCH="macpatch/mac.patch"
# Six of the patched files are committed with CRLF line endings; the patch carries them byte for
# byte, so git must not treat the CRs as trailing whitespace.
GA=(git apply --whitespace=nowarn)

is_applied() { "${GA[@]}" --reverse --check "$PATCH" >/dev/null 2>&1; }
applies_cleanly() { "${GA[@]}" --check "$PATCH" >/dev/null 2>&1; }

case "${1:-apply}" in
  apply)
    if [ "$(uname -s)" != "Darwin" ]; then
      echo "note: MacPatch is for macOS; this is $(uname -s). Applying anyway (see MACPATCH.md for what changes)."
    fi
    if is_applied; then
      echo "MacPatch is already applied."
      exit 0
    fi
    if applies_cleanly; then
      "${GA[@]}" "$PATCH"
      echo "MacPatch applied:"
    elif "${GA[@]}" --3way "$PATCH"; then
      echo "MacPatch applied with a 3-way merge (the code has moved on since the patch was made)."
      echo "The result is staged; review it with: git diff --cached"
    else
      echo "MacPatch does not apply: resolve the conflict markers by hand." >&2
      echo "Each change is described in macpatch/MACPATCH.md, so it can also be redone manually." >&2
      exit 1
    fi
    "${GA[@]}" --stat "$PATCH"
    echo
    echo "Next: .venv/bin/python -m audioviz check   then   .venv/bin/python tests/verify.py dummy"
    ;;
  --revert)
    if is_applied; then
      "${GA[@]}" --reverse "$PATCH"
      echo "MacPatch reverted."
    else
      echo "MacPatch is not applied (or the patched lines were edited since); nothing reverted." >&2
      exit 1
    fi
    ;;
  --status)
    if is_applied; then
      echo "applied"
    elif applies_cleanly; then
      echo "not applied (applies cleanly)"
    else
      echo "not applied (does not apply cleanly; see macpatch/MACPATCH.md)"
    fi
    ;;
  -h|--help)
    sed -n '2,7p' "$0"
    ;;
  *)
    echo "unknown option: $1 (try --help)" >&2
    exit 2
    ;;
esac
