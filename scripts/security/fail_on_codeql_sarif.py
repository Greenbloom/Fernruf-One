#!/usr/bin/env python3
"""Fail when a CodeQL SARIF file contains a warning or an error."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def finding_lines(document: dict) -> list[str]:
    lines: list[str] = []
    for run in document.get("runs") or []:
        if not isinstance(run, dict):
            continue
        rules = _rules(run)
        for result in run.get("results") or []:
            if not isinstance(result, dict) or result.get("suppressions"):
                continue
            if _level(result, rules) not in {"warning", "error"}:
                continue
            lines.append(_format_result(result))
    return lines


def _rules(run: dict) -> dict[str, dict]:
    tool = run.get("tool")
    driver = tool.get("driver") if isinstance(tool, dict) else None
    listed = driver.get("rules") if isinstance(driver, dict) else None
    if not isinstance(listed, list):
        return {}
    return {
        rule["id"]: rule
        for rule in listed
        if isinstance(rule, dict) and isinstance(rule.get("id"), str)
    }


def _level(result: dict, rules: dict[str, dict]) -> str:
    level = result.get("level")
    if isinstance(level, str) and level:
        return level.lower()
    rule_id = result.get("ruleId")
    rule = rules.get(rule_id) if isinstance(rule_id, str) else None
    config = rule.get("defaultConfiguration") if isinstance(rule, dict) else None
    default = config.get("level") if isinstance(config, dict) else None
    if isinstance(default, str) and default:
        return default.lower()
    return "warning"


def _format_result(result: dict) -> str:
    rule_id = result.get("ruleId") if isinstance(result.get("ruleId"), str) else "unknown-rule"
    message = result.get("message")
    text = message.get("text") if isinstance(message, dict) else ""
    if not isinstance(text, str):
        text = ""
    text = " ".join(text.split())
    if len(text) > 240:
        text = text[:240] + "..."
    place = _place(result)
    return " ".join(part for part in (rule_id, place, text) if part)


def _place(result: dict) -> str:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations or not isinstance(locations[0], dict):
        return ""
    physical = locations[0].get("physicalLocation")
    if not isinstance(physical, dict):
        return ""
    artifact = physical.get("artifactLocation")
    uri = artifact.get("uri") if isinstance(artifact, dict) else ""
    if not isinstance(uri, str):
        uri = ""
    region = physical.get("region")
    start = region.get("startLine") if isinstance(region, dict) else None
    if isinstance(start, int) and uri:
        return f"{uri}:{start}"
    if isinstance(start, int):
        return str(start)
    return uri


def sarif_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        if path.is_dir():
            files.extend(sorted(item for item in path.glob("*.sarif") if item.is_file()))
        elif path.is_file():
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        files = sarif_files(args.paths)
    except FileNotFoundError as exc:
        print(f"{exc}: not found", file=sys.stderr)
        return 1
    if not files:
        print("no CodeQL SARIF file found", file=sys.stderr)
        return 1

    found: list[str] = []
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1
        if not isinstance(document, dict):
            print(f"{path}: expected a SARIF object", file=sys.stderr)
            return 1
        found.extend(finding_lines(document))
    if not found:
        return 0
    print(f"{len(found)} CodeQL findings", file=sys.stderr)
    for line in found:
        print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
