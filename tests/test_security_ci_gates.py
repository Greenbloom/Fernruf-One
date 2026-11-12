from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORES = ROOT / "scripts" / "security" / "pip_audit_ignores.py"
SARIF = ROOT / "scripts" / "security" / "fail_on_codeql_sarif.py"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_missing_ignore_file_prints_nothing(tmp_path: Path) -> None:
    proc = _run(IGNORES, str(tmp_path / "missing.toml"), "--today", "2026-11-12")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_active_ignore_prints_its_id(tmp_path: Path) -> None:
    path = tmp_path / "ignores.toml"
    path.write_text(
        "\n".join(
            [
                "[[ignore]]",
                'id = "CVE-2024-1111"',
                'reason = "No fixed release on the 1.x line."',
                "expires = 2026-11-12",
                "",
                "[[ignore]]",
                'id = "GHSA-abcd-ef12-3456"',
                'reason = "The fix breaks the SIP invite path."',
                "expires = 2026-12-01",
                "",
            ]
        ),
        encoding="utf-8",
    )
    proc = _run(IGNORES, str(path), "--today", "2026-11-12")
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == ["CVE-2024-1111", "GHSA-abcd-ef12-3456"]
    assert proc.stderr == ""


def test_ignore_is_rejected_the_day_after_expiry(tmp_path: Path) -> None:
    path = tmp_path / "ignores.toml"
    path.write_text(
        "\n".join(
            [
                "[[ignore]]",
                'id = "PYSEC-2024-99"',
                'reason = "Waiting on the 2.3.4 release."',
                "expires = 2026-11-12",
                "",
            ]
        ),
        encoding="utf-8",
    )
    accepted = _run(IGNORES, str(path), "--today", "2026-11-12")
    rejected = _run(IGNORES, str(path), "--today", "2026-11-13")
    assert accepted.returncode == 0
    assert accepted.stdout.strip() == "PYSEC-2024-99"
    assert rejected.returncode == 1
    assert "PYSEC-2024-99" in rejected.stderr
    assert "expired" in rejected.stderr


def test_ignore_without_reason_fails(tmp_path: Path) -> None:
    path = tmp_path / "ignores.toml"
    path.write_text(
        "\n".join(
            [
                "[[ignore]]",
                'id = "CVE-2024-2222"',
                'reason = "   "',
                "expires = 2026-12-01",
                "",
            ]
        ),
        encoding="utf-8",
    )
    proc = _run(IGNORES, str(path), "--today", "2026-11-12")
    assert proc.returncode == 1
    assert "reason" in proc.stderr


def test_clean_sarif_exits_zero(tmp_path: Path) -> None:
    path = tmp_path / "python.sarif"
    path.write_text(json.dumps({"runs": [{"results": []}]}), encoding="utf-8")
    proc = _run(SARIF, str(path))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_warning_finding_fails_and_names_the_rule(tmp_path: Path) -> None:
    path = tmp_path / "python.sarif"
    path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "py/path-injection",
                                "level": "warning",
                                "message": {"text": "This path depends on a user-provided value."},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "fernruf/demo/video.py"},
                                            "region": {"startLine": 147},
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = _run(SARIF, str(path))
    assert proc.returncode == 1
    assert "py/path-injection" in proc.stderr
    assert "fernruf/demo/video.py:147" in proc.stderr


def test_note_and_suppressed_warning_do_not_fail(tmp_path: Path) -> None:
    path = tmp_path / "python.sarif"
    path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "results": [
                            {"ruleId": "py/note", "level": "note", "message": {"text": "style"}},
                            {
                                "ruleId": "py/clear-text-storage-sensitive-data",
                                "level": "error",
                                "suppressions": [{"kind": "inSource"}],
                                "message": {"text": "suppressed"},
                            },
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = _run(SARIF, str(path))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_directory_without_sarif_fails(tmp_path: Path) -> None:
    proc = _run(SARIF, str(tmp_path))
    assert proc.returncode == 1
    assert "no CodeQL SARIF file found" in proc.stderr
