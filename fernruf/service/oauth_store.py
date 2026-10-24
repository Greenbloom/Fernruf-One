"""OAuth clients, grants, and tokens. Access and refresh tokens are stored as hashes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import AuthorizationCode, RefreshToken, RegistrationError
from mcp.shared.auth import OAuthClientInformationFull

from fernruf.settings import Settings

_LOCK = threading.Lock()
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def redirect_uri_error(uri: str) -> str | None:
    """https everywhere. http only for a loopback host, which tests use."""
    parsed = urlparse(uri)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password:
        return "redirect URI must not include userinfo"
    if parsed.fragment:
        return "redirect URI must not include a fragment"
    if not host:
        return "redirect URI must include a host"
    if parsed.scheme == "https":
        return None
    if parsed.scheme == "http" and host in LOOPBACK_HOSTS:
        return None
    return "redirect URI must be https (loopback http is only allowed for tests)"


class OAuthStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.settings.call_settings_db)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        self._schema(conn)
        return conn

    def _schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_txn (
                txn_id TEXT PRIMARY KEY,
                json TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash TEXT PRIMARY KEY,
                json TEXT NOT NULL,
                expires_at REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_grants (
                grant_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                client_name TEXT NOT NULL,
                github_user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                refresh_expires_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS oauth_refresh (
                token_hash TEXT PRIMARY KEY,
                grant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at REAL NOT NULL,
                scopes TEXT NOT NULL,
                resource TEXT,
                subject TEXT
            );
            CREATE TABLE IF NOT EXISTS oauth_access (
                token_hash TEXT PRIMARY KEY,
                grant_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                scopes TEXT NOT NULL,
                resource TEXT,
                subject TEXT
            );
            """
        )

    def ensure(self) -> None:
        with _LOCK:
            conn = self._connect()
            conn.close()

    def client_count(self) -> int:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()
                return int(row["n"])
            finally:
                conn.close()

    def save_client(self, client: OAuthClientInformationFull) -> None:
        for uri in client.redirect_uris or []:
            reason = redirect_uri_error(str(uri))
            if reason:
                raise RegistrationError("invalid_redirect_uri", reason)
        with _LOCK:
            conn = self._connect()
            try:
                count = conn.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()["n"]
                if int(count) >= int(self.settings.oauth_max_clients):
                    raise RegistrationError(
                        "invalid_client_metadata",
                        "client registration limit reached",
                    )
                conn.execute(
                    "INSERT INTO oauth_clients (client_id, json) VALUES (?, ?)",
                    (client.client_id, client.model_dump_json()),
                )
                conn.commit()
            finally:
                conn.close()

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT json FROM oauth_clients WHERE client_id = ?",
                    (client_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["json"])

    def save_txn(self, txn_id: str, payload: dict[str, Any], expires_at: float) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO oauth_txn (txn_id, json, expires_at) VALUES (?, ?, ?)",
                    (txn_id, json.dumps(payload), expires_at),
                )
                conn.commit()
            finally:
                conn.close()

    def get_txn(self, txn_id: str) -> dict[str, Any] | None:
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT json, expires_at FROM oauth_txn WHERE txn_id = ?",
                    (txn_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None or float(row["expires_at"]) < time.time():
            return None
        data = json.loads(row["json"])
        return data if isinstance(data, dict) else None

    def update_txn(self, txn_id: str, payload: dict[str, Any]) -> None:
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE oauth_txn SET json = ? WHERE txn_id = ?",
                    (json.dumps(payload), txn_id),
                )
                conn.commit()
            finally:
                conn.close()

    def save_code(self, raw_code: str, code: AuthorizationCode) -> None:
        stored = code.model_dump(mode="json")
        stored.pop("code", None)
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO oauth_codes (code_hash, json, expires_at) VALUES (?, ?, ?)",
                    (token_hash(raw_code), json.dumps(stored), code.expires_at),
                )
                conn.commit()
            finally:
                conn.close()

    def load_code(self, client_id: str, raw_code: str) -> AuthorizationCode | None:
        digest = token_hash(raw_code)
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT json, consumed FROM oauth_codes WHERE code_hash = ?",
                    (digest,),
                ).fetchone()
            finally:
                conn.close()
        if row is None or int(row["consumed"]):
            return None
        data = json.loads(row["json"])
        data["code"] = raw_code
        code = AuthorizationCode.model_validate(data)
        if code.client_id != client_id:
            return None
        return code

    def consume_code(self, raw_code: str) -> bool:
        """Mark the code used. False if it was already consumed or missing."""
        digest = token_hash(raw_code)
        with _LOCK:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE oauth_codes SET consumed = 1 WHERE code_hash = ? AND consumed = 0",
                    (digest,),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def save_grant(
        self,
        *,
        grant_id: str,
        client_id: str,
        client_name: str,
        github_user_id: str,
        refresh_expires_at: float,
        access_token: str,
        refresh_token: str,
        access_expires_at: float,
        scopes: list[str],
        resource: str | None,
        subject: str,
    ) -> None:
        now = time.time()
        scope_text = " ".join(scopes)
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO oauth_grants (
                        grant_id, client_id, client_name, github_user_id,
                        created_at, refresh_expires_at, revoked
                    ) VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (grant_id, client_id, client_name, github_user_id, now, refresh_expires_at),
                )
                conn.execute(
                    """
                    INSERT INTO oauth_refresh (
                        token_hash, grant_id, status, expires_at, scopes, resource, subject
                    ) VALUES (?, ?, 'current', ?, ?, ?, ?)
                    """,
                    (token_hash(refresh_token), grant_id, refresh_expires_at, scope_text, resource, subject),
                )
                conn.execute(
                    """
                    INSERT INTO oauth_access (
                        token_hash, grant_id, client_id, expires_at, scopes, resource, subject
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash(access_token),
                        grant_id,
                        client_id,
                        access_expires_at,
                        scope_text,
                        resource,
                        subject,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_refresh(self, client_id: str, raw_token: str) -> RefreshToken | None:
        digest = token_hash(raw_token)
        copied: dict | None = None
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT r.scopes, r.expires_at, r.resource, r.subject, r.status,
                           r.grant_id, g.client_id, g.revoked
                    FROM oauth_refresh r
                    JOIN oauth_grants g ON g.grant_id = r.grant_id
                    WHERE r.token_hash = ?
                    """,
                    (digest,),
                ).fetchone()
                if row is None or row["client_id"] != client_id:
                    return None
                if int(row["revoked"]) or row["status"] == "used":
                    self._revoke_grant_locked(conn, row["grant_id"])
                    conn.commit()
                    return None
                copied = {
                    "scopes": row["scopes"],
                    "expires_at": int(row["expires_at"]),
                    "resource": row["resource"],
                    "subject": row["subject"],
                }
            finally:
                conn.close()
        assert copied is not None
        return RefreshToken(
            token=raw_token,
            client_id=client_id,
            scopes=[part for part in str(copied["scopes"]).split(" ") if part],
            expires_at=copied["expires_at"],
            resource=copied["resource"],
            subject=copied["subject"],
        )

    def rotate_refresh(
        self,
        *,
        old_refresh: str,
        new_refresh: str,
        new_access: str,
        client_id: str,
        scopes: list[str],
        access_expires_at: float,
        refresh_expires_at: float,
        resource: str | None,
        subject: str | None,
    ) -> bool:
        """Rotate the current refresh token. A reused token revokes the whole grant."""
        old_hash = token_hash(old_refresh)
        scope_text = " ".join(scopes)
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT r.grant_id, r.status, g.revoked, g.client_id
                    FROM oauth_refresh r
                    JOIN oauth_grants g ON g.grant_id = r.grant_id
                    WHERE r.token_hash = ?
                    """,
                    (old_hash,),
                ).fetchone()
                if row is None or row["client_id"] != client_id or int(row["revoked"]):
                    conn.rollback()
                    return False
                if row["status"] != "current":
                    self._revoke_grant_locked(conn, row["grant_id"])
                    conn.commit()
                    return False
                conn.execute(
                    "UPDATE oauth_refresh SET status = 'used' WHERE token_hash = ?",
                    (old_hash,),
                )
                conn.execute(
                    """
                    INSERT INTO oauth_refresh (
                        token_hash, grant_id, status, expires_at, scopes, resource, subject
                    ) VALUES (?, ?, 'current', ?, ?, ?, ?)
                    """,
                    (
                        token_hash(new_refresh),
                        row["grant_id"],
                        refresh_expires_at,
                        scope_text,
                        resource,
                        subject,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO oauth_access (
                        token_hash, grant_id, client_id, expires_at, scopes, resource, subject
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash(new_access),
                        row["grant_id"],
                        client_id,
                        access_expires_at,
                        scope_text,
                        resource,
                        subject,
                    ),
                )
                conn.execute(
                    "UPDATE oauth_grants SET refresh_expires_at = ? WHERE grant_id = ?",
                    (refresh_expires_at, row["grant_id"]),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def access_grant(self, raw_token: str) -> dict[str, Any] | None:
        digest = token_hash(raw_token)
        now = time.time()
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT a.client_id, a.expires_at, a.scopes, a.resource, a.subject, g.revoked
                    FROM oauth_access a
                    JOIN oauth_grants g ON g.grant_id = a.grant_id
                    WHERE a.token_hash = ?
                    """,
                    (digest,),
                ).fetchone()
            finally:
                conn.close()
        if row is None or int(row["revoked"]) or float(row["expires_at"]) <= now:
            return None
        return {
            "client_id": row["client_id"],
            "scopes": [part for part in str(row["scopes"]).split(" ") if part],
            "resource": row["resource"],
            "subject": row["subject"],
            "expires_at": int(row["expires_at"]),
        }

    def list_active_grants(self) -> list[dict[str, Any]]:
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT grant_id, client_id, client_name, github_user_id,
                           created_at, refresh_expires_at
                    FROM oauth_grants
                    WHERE revoked = 0
                    ORDER BY created_at
                    """
                ).fetchall()
            finally:
                conn.close()
        return [
            {
                "grant_id": row["grant_id"],
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "github_user_id": row["github_user_id"],
                "created_at": row["created_at"],
                "refresh_expires_at": row["refresh_expires_at"],
            }
            for row in rows
        ]

    def revoke_all(self) -> int:
        with _LOCK:
            conn = self._connect()
            try:
                cur = conn.execute("UPDATE oauth_grants SET revoked = 1 WHERE revoked = 0")
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    def _revoke_grant_locked(self, conn: sqlite3.Connection, grant_id: str) -> None:
        conn.execute("UPDATE oauth_grants SET revoked = 1 WHERE grant_id = ?", (grant_id,))
