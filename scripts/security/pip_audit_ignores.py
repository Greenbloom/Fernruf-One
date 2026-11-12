#!/usr/bin/env python3
"""Print active pip-audit ignore ids, or fail when an entry is invalid or expired."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

_ID = re.compile(r"^[A-Z][A-Z0-9]+(?:-[A-Za-z0-9]+)+$")
_FIELDS = frozenset({"id", "reason", "expires"})


class IgnoreFileError(Exception):
    pass


def active_ids(path: Path, today: date) -> list[str]:
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise IgnoreFileError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise IgnoreFileError(f"{path}: expected a table")
    unknown = sorted(set(data) - {"ignore"})
    if unknown:
        raise IgnoreFileError(f"{path}: unknown key {', '.join(unknown)}")
    entries = data.get("ignore", [])
    if not isinstance(entries, list):
        raise IgnoreFileError(f"{path}: ignore must be an array of tables")

    ids: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        vuln_id = _entry_id(path, index, entry, today)
        if vuln_id in seen:
            raise IgnoreFileError(f"{path}: duplicate id {vuln_id}")
        seen.add(vuln_id)
        ids.append(vuln_id)
    return ids


def _entry_id(path: Path, index: int, entry: object, today: date) -> str:
    label = f"{path}: ignore entry {index}"
    if not isinstance(entry, dict):
        raise IgnoreFileError(f"{label} must be a table")
    unknown = sorted(set(entry) - _FIELDS)
    if unknown:
        raise IgnoreFileError(f"{label} has unknown key {', '.join(unknown)}")
    missing = sorted(_FIELDS - set(entry))
    if missing:
        raise IgnoreFileError(f"{label} is missing {', '.join(missing)}")

    vuln_id = entry["id"]
    if not isinstance(vuln_id, str) or _ID.fullmatch(vuln_id) is None:
        raise IgnoreFileError(f"{label} has an invalid id")
    reason = entry["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise IgnoreFileError(f"{label} needs a reason")
    expires = entry["expires"]
    if isinstance(expires, datetime) or not isinstance(expires, date):
        raise IgnoreFileError(f"{label} expires must be a date")
    if today > expires:
        raise IgnoreFileError(f"{label} {vuln_id} expired on {expires.isoformat()}")
    return vuln_id


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--today", type=_parse_day, default=None)
    args = parser.parse_args(argv)
    today = args.today if args.today is not None else datetime.now(UTC).date()
    try:
        ids = active_ids(args.path, today)
    except IgnoreFileError as exc:
        print(exc, file=sys.stderr)
        return 1
    for vuln_id in ids:
        print(vuln_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
