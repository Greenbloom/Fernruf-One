"""Outbound notices. Service start is the only event wired up so far.

Later events (a new OAuth sign-in, an auth lockout) should build a Notice and
call Notifier.deliver_in_background. The SMTP path stays here.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import socket
import ssl
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from fernruf.quiet_hours import BERLIN
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.startup")
# First try plus three retries. A failure is logged and never raised to the caller.
SEND_ATTEMPTS = 4
_REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Notice:
    kind: str
    subject: str
    body: str


class _VerifiedSMTP(smtplib.SMTP):
    """STARTTLS checked against a name that is not the dial address.

    Postfix at 127.0.0.1 presents a certificate for mail.example.com. The
    stdlib verifies that certificate against the host we connected to, which
    is the tunnel IP, so this subclass passes the certificate name instead.
    Hostname checking stays on.
    """

    def __init__(self, *args, tls_name: str, **kwargs) -> None:
        self._tls_name = tls_name
        super().__init__(*args, **kwargs)

    def starttls(self, *, context: ssl.SSLContext | None = None):
        self.ehlo_or_helo_if_needed()
        if not self.has_extn("starttls"):
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
        resp, reply = self.docmd("STARTTLS")
        if resp != 220:
            raise smtplib.SMTPResponseException(resp, reply)
        if context is None:
            context = ssl.create_default_context()
        if context.check_hostname is not True or context.verify_mode != ssl.CERT_REQUIRED:
            raise RuntimeError("STARTTLS must verify the certificate hostname")
        self.sock = context.wrap_socket(self.sock, server_hostname=self._tls_name)
        self.file = None
        self.helo_resp = None
        self.ehlo_resp = None
        self.esmtp_features = {}
        self.does_esmtp = False
        return resp, reply


def _git_fact(args: list[str]) -> str:
    try:
        text = subprocess.check_output(
            ["git", *args],
            cwd=_REPO,
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not text or len(text) > 80 or any(ch.isspace() for ch in text):
        return "unknown"
    return text


def auth_mode_label(*, github: bool, static_token: bool) -> str:
    oauth = "oauth/github" if github else "oauth/github:off"
    static = "static:on" if static_token else "static:off"
    if github:
        return f"oauth/github+{static}"
    return static


class Notifier:
    def __init__(self, settings: Settings, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self.settings = settings
        self._ssl_context = ssl_context

    def send_sync(self, notice: Notice) -> None:
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.notify_from
        message["To"] = settings.notify_to
        message["Subject"] = notice.subject
        message.set_content(notice.body)
        context = self._ssl_context if self._ssl_context is not None else ssl.create_default_context()
        tls_name = (settings.notify_smtp_tls_name or "").strip() or (
            settings.notify_smtp_host or ""
        ).strip()
        try:
            with _VerifiedSMTP(
                settings.notify_smtp_host,
                settings.notify_smtp_port,
                local_hostname=settings.notify_smtp_ehlo,
                timeout=settings.notify_timeout_s,
                tls_name=tls_name,
            ) as smtp:
                smtp.ehlo(settings.notify_smtp_ehlo)
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=context)
                    smtp.ehlo(settings.notify_smtp_ehlo)
                elif settings.notify_smtp_require_tls:
                    raise smtplib.SMTPNotSupportedError("STARTTLS is required")
                smtp.send_message(message)
        except Exception as exc:
            LOG.warning(
                "fernruf notify SMTP failed host=%s port=%s tls_name=%s: %s",
                settings.notify_smtp_host,
                settings.notify_smtp_port,
                tls_name,
                exc,
            )
            raise

    async def deliver(self, notice: Notice) -> bool:
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.send_sync, notice),
                    timeout=float(self.settings.notify_timeout_s) + 1,
                )
            except Exception:
                LOG.warning("fernruf notify failed event=%s attempt=%s", notice.kind, attempt)
                if attempt < SEND_ATTEMPTS:
                    await asyncio.sleep(0.2)
                continue
            LOG.info("fernruf notify sent event=%s", notice.kind)
            return True
        return False

    def deliver_in_background(self, notice: Notice) -> None:
        task = asyncio.get_running_loop().create_task(self.deliver(notice))

        def _done(finished: asyncio.Task[bool]) -> None:
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                LOG.warning("fernruf notify failed event=%s", notice.kind)

        task.add_done_callback(_done)


def build_service_started(settings: Settings, *, github: bool, static_token: bool) -> Notice:
    from datetime import datetime

    from fernruf.call_settings.store import get_call_settings

    sha = _git_fact(["rev-parse", "HEAD"])
    branch = _git_fact(["rev-parse", "--abbrev-ref", "HEAD"])
    try:
        allowlist_size: object = len(get_call_settings(settings).allowlist)
    except Exception:
        allowlist_size = "unknown"
    when = datetime.now(tz=BERLIN).isoformat(timespec="seconds")
    mode = auth_mode_label(github=github, static_token=static_token)
    body = "\n".join(
        [
            "event: service_started",
            f"time: {when}",
            f"host: {socket.gethostname()}",
            f"git_sha: {sha}",
            f"git_branch: {branch}",
            f"bind: {settings.bind_host}:{settings.bind_port}",
            f"auth: {mode}",
            f"allowlist_size: {allowlist_size}",
            f"calls_per_day: {settings.calls_per_day}",
            f"minutes_per_day: {settings.minutes_per_day}",
            f"monthly_budget_eur: {settings.monthly_budget_eur}",
            f"mobile_budget_eur: {settings.mobile_budget_eur}",
            f"max_minutes_cap: {settings.max_minutes_cap}",
            "",
        ]
    )
    return Notice(
        kind="service_started",
        subject=f"fernruf started on the host ({sha})",
        body=body,
    )


def announce_service_started(
    settings: Settings,
    *,
    github: bool,
    static_token: bool,
    notifier: Notifier | None = None,
) -> None:
    """Log one stable line, then email. Neither step raises."""
    try:
        notice = build_service_started(settings, github=github, static_token=static_token)
    except Exception:
        LOG.warning("fernruf started bind=%s:%s sha=unknown auth=unknown", settings.bind_host, settings.bind_port)
        return
    mode = auth_mode_label(github=github, static_token=static_token)
    sha = notice.subject.removeprefix("fernruf started on the host (").removesuffix(")")
    LOG.info("fernruf started bind=%s:%s sha=%s auth=%s", settings.bind_host, settings.bind_port, sha, mode)
    if not settings.notify_enabled:
        return
    try:
        (notifier or Notifier(settings)).deliver_in_background(notice)
    except Exception:
        LOG.warning("fernruf notify failed event=service_started")
