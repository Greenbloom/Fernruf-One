"""Runtime code must not embed the demo name Alex (see README / .env.example for docs)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FERNRUF = ROOT / "fernruf"


def test_fernruf_package_source_has_no_alex() -> None:
    hits: list[str] = []
    for path in sorted(FERNRUF.rglob("*")):
        if not path.is_file() or path.suffix in {".pyc", ".wav"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "Alex" not in line:
                continue
            if "PRINCIPAL_ENV_PLACEHOLDER" in line:
                continue
            hits.append(f"{path.relative_to(ROOT)}:{lineno}:{line.strip()}")
    assert not hits, (
        "fernruf/ must not contain the substring 'Alex' outside PRINCIPAL_ENV_PLACEHOLDER:\n"
        + "\n".join(hits)
    )
