#!/usr/bin/env bash
# Hermetic policy-check rejects using .env.verify (no Docker/SIPp). Used by CI and verify-run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
cd "$ROOT"
# shellcheck source=scripts/verify/common.sh
source "$ROOT/scripts/verify/common.sh"
verify_run_policy_pchecks "$ROOT/verify-evidence"
echo "verify policy pchecks=pass"
