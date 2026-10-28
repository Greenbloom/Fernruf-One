"""Create and append runtime files under ``./data``."""

from __future__ import annotations

import errno
import os
from pathlib import Path

# Docker bind-mounts these. If they are missing, the daemon creates ``./data``
# as root and later writes (audit, latency, generated sounds) fail.
DATA_DIR_FIX = "mkdir -p data/asterisk-sounds data/asterisk-generated"


def permission_message(path: Path) -> str:
    return (
        f"Cannot write {path}: permission denied. "
        "Create the data directories as your user before 'docker compose up' "
        f"so Docker does not create ./data as root: {DATA_DIR_FIX}"
    )


def _is_permission(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM)


def prepare_parent(path: Path) -> None:
    """Create ``path``'s parent. Permission errors name the fresh-clone fix."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if _is_permission(exc):
            raise PermissionError(permission_message(path)) from exc
        raise
    # mkdir(exist_ok=True) succeeds when Docker already created the directory
    # as root. The following open is what used to raise a bare Errno 13.
    if path.parent.is_dir() and not os.access(path.parent, os.W_OK):
        raise PermissionError(permission_message(path))


def open_append(path: Path):
    """Open ``path`` for append, creating the parent directory first."""
    prepare_parent(path)
    try:
        return path.open("a", encoding="utf-8")
    except OSError as exc:
        if _is_permission(exc):
            raise PermissionError(permission_message(path)) from exc
        raise
