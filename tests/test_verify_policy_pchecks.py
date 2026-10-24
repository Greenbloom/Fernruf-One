from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_verify_run_policy_pchecks_fails_on_first_wrong_reason() -> None:
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'''
set -euo pipefail
ROOT="{root}"
source "$ROOT/scripts/verify/common.sh"
run_one() {{
  verify_common_init
  verify_write_env_file "$ROOT/verify-evidence"
  PCHECK=(verify_fernruf_env env FERNRUF_INTERNATIONAL_ENABLED=true "FERNRUF_ALLOWLIST=${{POLICY_ALLOWLIST}}")
  verify_expect_reject "blocked internal **99" "not_the_real_reason" \\
    "${{PCHECK[@]}}" uv run fernruf policy-check "**99" || return 1
}}
run_one
''',
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "not_the_real_reason" in proc.stderr or "VERIFY_FAIL" in proc.stderr


def test_run_policy_pchecks_script_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "verify" / "run_policy_pchecks.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "verify policy pchecks=pass" in proc.stdout


def test_load_settings_bad_quiet_hours_one_line_error(tmp_path: Path) -> None:
    env = tmp_path / "bad.env"
    env.write_text("FERNRUF_QUIET_HOURS=off\n", encoding="utf-8")
    child_env = os.environ.copy()
    child_env["FERNRUF_ENV_FILE"] = str(env)
    child_env["FERNRUF_QUIET_HOURS_ALLOW_OFF"] = "false"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "from fernruf.settings import load_settings; load_settings()",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "FERNRUF_QUIET_HOURS_ALLOW_OFF" in proc.stderr + proc.stdout
    assert "Traceback" not in proc.stderr + proc.stdout
