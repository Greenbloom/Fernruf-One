from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _p(*chunks: str) -> str:
    """Build phone-like literals at runtime so the gate does not match this test module."""
    return "".join(chunks)


def _minimal_privacy_gate_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    for name in ("README.md", "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md"):
        (root / name).write_text("# stub\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "fernruf").mkdir()
    (root / "deploy").mkdir()
    (root / "scripts").mkdir()
    shutil.copy2(_REPO / "scripts" / "privacy_gate.sh", root / "scripts" / "privacy_gate.sh")
    (root / ".env.example").write_text("# stub\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_privacy_gate_fails_on_missing_security_and_private_ip(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    shutil.copytree(Path(__file__).resolve().parents[1], root, ignore=shutil.ignore_patterns(".git", ".venv", "verify-evidence"))
    sec = root / "SECURITY.md"
    if sec.is_file():
        sec.unlink()
    (root / "docs" / "trap.md").write_text("bind 203.0.113.50\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SECURITY.md" in proc.stderr or "203.0.113" in proc.stderr


def test_privacy_gate_fails_on_spaced_mobile_in_source(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-phone.md").write_text(
        f"Call {_p('+49 17', '6 1234 5678')} anytime.\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "phone" in proc.stderr.lower()


def test_privacy_gate_fails_on_national_format_in_source(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-national.md").write_text(
        f"dial {_p('017', '6 12345678')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "phone" in proc.stderr.lower()


def test_privacy_gate_fails_on_realistic_berlin_spaced(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-berlin.md").write_text(
        f"Office {_p('+49 30 ', '87654321')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_fails_on_realistic_0151(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-151.md").write_text(
        f"Mobil {_p('0151 ', '23456789')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_fails_on_second_number_on_line(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-dual.md").write_text(
        f"Allowlisted +493023125123, backup {_p('+49 17', '6 1111222')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_fails_on_unseparated_national(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-compact-national.md").write_text(
        f"fax {_p('030', '87654321')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


_REPO_EXAMPLE_NUMBERS = (
    "+493023125123",
    "+493023125124",
    "+499999999999",
    "+499001234567",
    "+491180000000",
    "+491713920011",
    "03023125123",
    "+491713920000",
    "+12025550100",
)


def test_privacy_gate_allows_repo_example_numbers(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    lines = [f"example {n}\n" for n in _REPO_EXAMPLE_NUMBERS]
    (root / "docs" / "examples.md").write_text("".join(lines), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_privacy_gate_fails_on_slash_separator_phone(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-slash.md").write_text(
        f"fax {_p('030/', '25478163')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_fails_on_paren_separator_phone(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-paren.md").write_text(
        f"office {_p('(030) ', '25478163')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_fails_on_dotted_berlin_phone(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-dotted-berlin.md").write_text(
        f"line {_p('030.', '2547.', '8163')}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "docs/trap-dotted-berlin.md"], cwd=root, check=True)
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "trap-dotted-berlin" in proc.stderr
    assert "phone" in proc.stderr.lower()


def test_privacy_gate_fails_on_dotted_mobile_phone(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-dotted-mobile.md").write_text(
        f"mobile {_p('0176.', '2345.', '6789')}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "docs/trap-dotted-mobile.md"], cwd=root, check=True)
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "phone" in proc.stderr.lower()
    assert "trap-dotted-mobile" in proc.stderr


def test_privacy_gate_fails_on_number_in_env_example(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / ".env.example").write_text(
        f"FERNRUF_ALLOWLIST={_p('+49 17', '6 5554433')}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".env.example"], cwd=root, check=True)
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "phone" in proc.stderr.lower()
    assert ".env.example" in proc.stderr


def test_privacy_gate_fails_on_short_area_code_style(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-short.md").write_text(
        f"Rückruf {_p('07071 ', '234567')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_ignores_common_non_phone_literals(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "pyproject.toml").write_text('requires-python = ">=3.12"\n', encoding="utf-8")
    (root / "docker-compose.yml").write_text("version: '3.9'\n", encoding="utf-8")
    (root / ".github").mkdir()
    (root / ".github" / "workflows").mkdir()
    (root / ".github" / "workflows" / "ci.yml").write_text(
        'GITLEAKS_VERSION: "8.24.2"\n--at-iso "2026-06-15T21:00:00+02:00"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_privacy_gate_scans_hidden_github_dir(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / ".github").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "trap.yml").write_text(
        f"contact: {_p('+49 17', '6 9998877')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_privacy_gate_passes_on_real_repo_tree() -> None:
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_privacy_gate_fails_when_rg_errors(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "rg").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    (fake_bin / "rg").chmod(0o755)
    root = _minimal_privacy_gate_tree(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "rg error" in proc.stderr.lower()


def test_privacy_gate_no_git_fails_on_funding_like_git_mode(tmp_path: Path) -> None:
    root = tmp_path / "zip-tree"
    root.mkdir()
    for name in ("README.md", "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md"):
        (root / name).write_text("# stub\n", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(_REPO / "scripts" / "privacy_gate.sh", root / "scripts" / "privacy_gate.sh")
    (root / "docs").mkdir()
    (root / "docs" / "funding-trap.md").write_text(
        f"see {_p('ko-', 'fi.com/example')}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "banned_term" in proc.stderr
    assert _p("ko-", "fi.com") not in proc.stderr


def test_privacy_gate_fails_on_short_dotted_local_number(tmp_path: Path) -> None:
    root = _minimal_privacy_gate_tree(tmp_path)
    (root / "docs" / "trap-short-dotted.md").write_text(
        f"branch {_p('07071.', '2345')}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "docs/trap-short-dotted.md"], cwd=root, check=True)
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "phone" in proc.stderr.lower()
    assert "trap-short-dotted" in proc.stderr


def test_privacy_gate_no_git_skips_env_and_redacts_trap(tmp_path: Path) -> None:
    root = tmp_path / "zip-tree"
    root.mkdir()
    for name in ("README.md", "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md"):
        (root / name).write_text("# stub\n", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(_REPO / "scripts" / "privacy_gate.sh", root / "scripts" / "privacy_gate.sh")
    trap = _p("+49 17", "6 5554433")
    (root / ".env").write_text(f"FERNRUF_ALLOWLIST={trap}\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "ok.md").write_text("allowlisted +493023125123\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert trap not in proc.stderr
    assert ".env" not in proc.stderr or "private_ip" in proc.stderr


def test_privacy_gate_passes_under_bash_32_if_available() -> None:
    bash32 = Path("/bin/bash")
    for candidate in ("/usr/local/bin/bash32", "/opt/bash-3.2/bin/bash"):
        if Path(candidate).is_file():
            bash32 = Path(candidate)
            break
    proc = subprocess.run(
        [str(bash32), "--version"],
        capture_output=True,
        text=True,
    )
    if "3.2" not in proc.stdout + proc.stderr:
        return
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [str(bash32), str(root / "scripts" / "privacy_gate.sh")],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
