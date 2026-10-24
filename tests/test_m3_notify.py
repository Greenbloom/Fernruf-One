"""Startup mail: STARTTLS is required and the certificate name is checked."""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from fernruf.service.notifier import Notice, Notifier, announce_service_started, build_service_started
from fernruf.service.startup import FernrufServer
from fernruf.settings import Settings
from tests.m3_support import service_settings

PHONE = "+493023125123"
TOKEN = "mcp-token-SHOULD_NOT_APPEAR_in_mail_or_logs_0123456789abcdef"
GITHUB_SECRET = "ghsec-SHOULD_NOT_APPEAR_in_mail_or_logs_0123456789abcdef"
BODY_MARKER = "BODYMARKER_service_started_plain"


def test_notify_defaults_name_the_certificate_and_require_tls() -> None:
    settings = Settings(_env_file=None)
    assert settings.notify_enabled is False
    assert settings.notify_smtp_host == ""
    assert settings.notify_smtp_port == 587
    assert settings.notify_smtp_tls_name == ""
    assert settings.notify_smtp_require_tls is True
    assert settings.notify_smtp_ehlo == ""
    assert settings.notify_from == ""
    assert settings.notify_to == ""
    context = ssl.create_default_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.fixture(scope="module")
def mail_cert(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("mailcert")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mail.example.com")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("mail.example.com")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path


def _client_context(cert_path: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(cert_path))
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    return context


def _read_line(conn: socket.socket, buf: bytes) -> tuple[str | None, bytes]:
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None, buf
        buf += chunk
    raw, buf = buf.split(b"\n", 1)
    return raw.decode("utf-8", "replace").strip(), buf


class _Inbox:
    """Minimal SMTP sink. Connect to 127.0.0.1; the certificate is for mail.example.com."""

    def __init__(self, cert_path: Path, *, offer_tls: bool = True, fail_first: int = 0) -> None:
        self.offer_tls = offer_tls
        self.fail_first = fail_first
        self.commands: list[str] = []
        self.messages: list[str] = []
        self.connections = 0
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        key_path = cert_path.parent / "key.pem"
        self._ctx.load_cert_chain(cert_path, key_path)
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)

    def __enter__(self) -> _Inbox:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            if self.fail_first > 0:
                self.fail_first -= 1
                conn.close()
                continue
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5)
            conn.sendall(b"220 fake ESMTP\r\n")
            buf = b""
            tls = False
            while True:
                line, buf = _read_line(conn, buf)
                if line is None:
                    break
                self.commands.append(line)
                verb, _, _rest = line.partition(" ")
                verb = verb.upper()
                if verb in ("EHLO", "HELO"):
                    if self.offer_tls and not tls:
                        conn.sendall(b"250-fake\r\n250-STARTTLS\r\n250 HELP\r\n")
                    else:
                        conn.sendall(b"250 HELP\r\n")
                elif verb == "STARTTLS":
                    conn.sendall(b"220 Go ahead\r\n")
                    conn = self._ctx.wrap_socket(conn, server_side=True)
                    tls = True
                    buf = b""
                elif verb == "DATA":
                    conn.sendall(b"354 go\r\n")
                    chunks: list[str] = []
                    while True:
                        data_line, buf = _read_line(conn, buf)
                        if data_line is None or data_line == ".":
                            break
                        chunks.append(data_line)
                    self.messages.append("\n".join(chunks))
                    conn.sendall(b"250 queued\r\n")
                elif verb == "QUIT":
                    conn.sendall(b"221 bye\r\n")
                    break
                else:
                    conn.sendall(b"250 OK\r\n")
        except (OSError, ssl.SSLError, TimeoutError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                return


def _settings(tmp_path: Path, port: int, **overrides: object) -> tuple[Settings, str]:
    values: dict[str, object] = {
        "FERNRUF_NOTIFY_ENABLED": True,
        "FERNRUF_NOTIFY_SMTP_HOST": "127.0.0.1",
        "FERNRUF_NOTIFY_SMTP_PORT": port,
        "FERNRUF_NOTIFY_SMTP_TLS_NAME": "mail.example.com",
        "FERNRUF_NOTIFY_SMTP_REQUIRE_TLS": True,
        "FERNRUF_NOTIFY_SMTP_EHLO": "the host.example.com",
        "FERNRUF_NOTIFY_TIMEOUT_S": 3,
        "FERNRUF_MCP_TOKEN": TOKEN,
        "FERNRUF_GITHUB_CLIENT_SECRET": GITHUB_SECRET,
        "FERNRUF_ALLOWLIST": PHONE,
    }
    values.update(overrides)
    return service_settings(tmp_path, **values)


def _notice() -> Notice:
    return Notice(kind="service_started", subject="fernruf started on the host (abc)", body=BODY_MARKER + "\n")


def test_starttls_verifies_mail_name_not_the_dial_address(tmp_path: Path, mail_cert: Path) -> None:
    context = _client_context(mail_cert)
    with _Inbox(mail_cert) as inbox:
        settings, _token = _settings(tmp_path, inbox.port)
        Notifier(settings, ssl_context=context).send_sync(_notice())
        ehlo = [line.lower() for line in inbox.commands if line.lower().startswith("ehlo ")]
        assert ehlo == ["ehlo the host.example.com", "ehlo the host.example.com"]
        assert any(line.upper() == "STARTTLS" for line in inbox.commands)
        assert len(inbox.messages) == 1
        mailed = inbox.messages[0]
        assert "Subject: fernruf started on the host (abc)" in mailed
        assert BODY_MARKER in mailed
        assert TOKEN not in mailed
        assert GITHUB_SECRET not in mailed
        assert PHONE not in mailed


def test_wrong_tls_name_is_rejected(tmp_path: Path, mail_cert: Path) -> None:
    context = _client_context(mail_cert)
    with _Inbox(mail_cert) as inbox:
        settings, _token = _settings(tmp_path, inbox.port, FERNRUF_NOTIFY_SMTP_TLS_NAME="wrong.example")
        with pytest.raises(ssl.SSLCertVerificationError):
            Notifier(settings, ssl_context=context).send_sync(_notice())
        assert inbox.messages == []


def test_hostname_checking_cannot_be_turned_off(tmp_path: Path, mail_cert: Path) -> None:
    context = _client_context(mail_cert)
    context.check_hostname = False
    with _Inbox(mail_cert) as inbox:
        settings, _token = _settings(tmp_path, inbox.port)
        with pytest.raises(RuntimeError, match="verify the certificate hostname"):
            Notifier(settings, ssl_context=context).send_sync(_notice())
        assert inbox.messages == []


def test_cleartext_is_refused_when_starttls_is_required(tmp_path: Path, mail_cert: Path) -> None:
    import smtplib

    with _Inbox(mail_cert, offer_tls=False) as inbox:
        settings, _token = _settings(tmp_path, inbox.port)
        with pytest.raises(smtplib.SMTPNotSupportedError):
            Notifier(settings, ssl_context=_client_context(mail_cert)).send_sync(_notice())
        assert inbox.messages == []
        assert not any(line.upper() == "STARTTLS" for line in inbox.commands)


def test_ehlo_name_is_configurable(tmp_path: Path, mail_cert: Path) -> None:
    with _Inbox(mail_cert) as inbox:
        settings, _token = _settings(tmp_path, inbox.port, FERNRUF_NOTIFY_SMTP_EHLO="other.example")
        Notifier(settings, ssl_context=_client_context(mail_cert)).send_sync(_notice())
        ehlo = [line.lower() for line in inbox.commands if line.lower().startswith("ehlo ")]
        assert ehlo == ["ehlo other.example", "ehlo other.example"]


def test_deliver_retries_then_sends(tmp_path: Path, mail_cert: Path, caplog: pytest.LogCaptureFixture) -> None:
    context = _client_context(mail_cert)
    with _Inbox(mail_cert, fail_first=3) as inbox:
        settings, _token = _settings(tmp_path, inbox.port)
        notifier = Notifier(settings, ssl_context=context)
        with caplog.at_level(logging.INFO, logger="fernruf.startup"):
            assert asyncio.run(notifier.deliver(_notice())) is True
        assert inbox.connections == 4
        assert len(inbox.messages) == 1
        assert "fernruf notify sent event=service_started" in caplog.text
        assert "fernruf notify failed event=service_started attempt=1" in caplog.text
        assert "fernruf notify failed event=service_started attempt=3" in caplog.text
        assert BODY_MARKER not in caplog.text
        assert TOKEN not in caplog.text
        assert GITHUB_SECRET not in caplog.text
        assert PHONE not in caplog.text


def test_service_started_mail_has_facts_and_no_secrets(tmp_path: Path, mail_cert: Path) -> None:
    with _Inbox(mail_cert) as inbox:
        settings, _token = _settings(tmp_path, inbox.port)
        notice = build_service_started(settings, github=True, static_token=True)
        Notifier(settings, ssl_context=_client_context(mail_cert)).send_sync(notice)
        mailed = inbox.messages[0]
        assert notice.subject.startswith("fernruf started on the host (")
        assert notice.subject in mailed
        assert "event: service_started" in mailed
        assert "auth: oauth/github+static:on" in mailed
        assert f"bind: {settings.bind_host}:{settings.bind_port}" in mailed
        assert "allowlist_size: 1" in mailed
        assert "calls_per_day: 10" in mailed
        assert TOKEN not in mailed
        assert GITHUB_SECRET not in mailed
        assert PHONE not in mailed
        assert TOKEN not in notice.subject
        assert PHONE not in notice.body


def test_disabled_notify_logs_the_start_and_does_not_connect(
    tmp_path: Path, mail_cert: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with _Inbox(mail_cert) as inbox:
        settings, _token = _settings(tmp_path, inbox.port, FERNRUF_NOTIFY_ENABLED=False)

        async def run() -> None:
            announce_service_started(settings, github=False, static_token=True)
            await asyncio.sleep(0.3)

        with caplog.at_level(logging.INFO, logger="fernruf.startup"):
            asyncio.run(run())
        assert f"fernruf started bind={settings.bind_host}:{settings.bind_port} sha=" in caplog.text
        assert "auth=static:on" in caplog.text
        assert "notify sent" not in caplog.text
        assert "notify failed" not in caplog.text
        assert inbox.connections == 0


def test_startup_announces_only_after_the_socket_is_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings, _token = service_settings(tmp_path, FERNRUF_NOTIFY_ENABLED=False)
    seen: dict[str, bool] = {}

    async def app(scope, receive, send):
        if scope["type"] != "lifespan":
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    from fernruf.service import startup as startup_mod

    real = startup_mod.announce_service_started

    def spy(bound_settings, *, github, static_token, notifier=None):
        seen["started"] = server.started
        sock = server.servers[0].sockets[0]
        with socket.create_connection(sock.getsockname(), timeout=2):
            seen["accepts"] = True
        server.should_exit = True
        real(bound_settings, github=github, static_token=static_token, notifier=notifier)

    monkeypatch.setattr(startup_mod, "announce_service_started", spy)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", access_log=False)
    server = FernrufServer(config, settings, github=False, static_token=True)

    async def run_server() -> None:
        await server._serve()

    with caplog.at_level(logging.INFO, logger="fernruf.startup"):
        asyncio.run(run_server())
    assert seen == {"started": True, "accepts": True}
    assert f"fernruf started bind={settings.bind_host}:{settings.bind_port} sha=" in caplog.text
    assert "notify failed" not in caplog.text
