"""FastAPI front door and the twelve MCP tools."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from fernruf.service.guide import guide_for_settings
from fernruf.service.mcp_hosts import mcp_allowed_hosts
from fernruf.service.line_health import LineHealth, line_health_for
from fernruf.service.manager import CallError, CallManager, limits_for
from fernruf.service.records import save_webhook
from fernruf.call_settings.store import get_call_settings
from fernruf.service.github_oauth import GitHubLogin, oauth_routes
from fernruf.service.oauth_provider import FernrufOAuthProvider
from fernruf.service.oauth_store import OAuthStore
from fernruf.service.security import (
    AUTH_LOG_LINE,
    AuthLockout,
    WindowLimiter,
    bearer_matches,
    client_ip,
    require_login_configured,
)
from fernruf.service.settings_update import apply_mcp_settings, public_settings
from fernruf.service.webhooks import webhook_url_error
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.http")
AUTH_LOG = logging.getLogger("fernruf.auth")

WEBHOOK_EVENTS = [
    "call.queued",
    "call.dialing",
    "call.answered",
    "call.finalized",
    "call.recording_ready",
]
_MAX_BODY = 64 * 1024


class CallTaskIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    goal: str
    context: str = ""
    questions: list[str] = Field(default_factory=list)
    must_say: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    decision_limits: str = ""
    fallback: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        if self.model_extra:
            data.update(self.model_extra)
        return data


class ServiceRuntime:
    def __init__(
        self,
        settings: Settings,
        manager: CallManager,
        lockout: AuthLockout,
        poster,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.lockout = lockout
        self.poster = poster
        self.token = require_login_configured(
            settings.mcp_token,
            settings.github_client_id,
            settings.github_client_secret,
        )
        self.store = OAuthStore(settings)
        self.oauth_limiter = WindowLimiter(max(1, int(settings.oauth_rate_per_min)))
        resource = (settings.oauth_resource or "").strip()
        if resource:
            self.resource_metadata_url = str(
                build_resource_metadata_url(AnyHttpUrl(resource))
            )
        else:
            self.resource_metadata_url = ""
        self.guide_sessions: set[str] = set()


def session_key(ctx: Context) -> str:
    """Stable id for one MCP session. ServerSession itself is per request."""
    session = ctx.request_context.session
    connection = getattr(session, "_connection", None)
    if connection is not None:
        sid = getattr(connection, "session_id", None)
        if sid:
            return str(sid)
        return f"conn:{id(connection)}"
    return f"session:{id(session)}"


def _tool_error(code: str, message: str, **extra: Any) -> None:
    payload = {"error_code": code, "message": message, **extra}
    raise ToolError(json.dumps(payload, ensure_ascii=False))


def _raise_call(exc: CallError) -> None:
    _tool_error(exc.code, exc.message, **exc.extra)


async def _default_poster(url: str, body: dict[str, Any]) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError:
        return False
    return 200 <= response.status_code < 300


def build_mcp(runtime: ServiceRuntime) -> MCPServer:
    mcp = MCPServer(
        "fernruf",
        instructions=(
            "AI phone calls for agents via your SIP line. "
            "Read get_calling_guide before any write. Confirm the number and task with the operator. "
            "Do not place marketing calls. The model is configured per call and is not fixed."
        ),
    )

    def require_guide(ctx: Context) -> None:
        if session_key(ctx) not in runtime.guide_sessions:
            _tool_error(
                "guide_required",
                "Call get_calling_guide in this MCP session before using a write tool.",
            )

    @mcp.tool()
    async def get_calling_guide(ctx: Context) -> str:
        """How to place a call, the current limits, and whether the phone line is up.

        Read this before place_call, place_call_and_wait, hangup_call,
        update_call_settings, or connect_webhook. Writes fail until you do.
        """
        limits = limits_for(runtime.settings)
        text = guide_for_settings(
            runtime.settings,
            limits,
            line_ready=runtime.manager.line_health.ready(),
            now=runtime.manager.now(),
        )
        runtime.guide_sessions.add(session_key(ctx))
        return text

    @mcp.tool()
    def get_balance() -> dict[str, Any]:
        """Monthly budget, spend, and how many calls have been placed today."""
        return runtime.manager.balance()

    @mcp.tool()
    def get_call_settings() -> dict[str, Any]:
        """Voice, limits, allowlist, and quiet hours. The allowlist is read-only here."""
        data = public_settings(runtime.settings)
        data["allowlist_enforced"] = True
        return data

    @mcp.tool()
    def update_call_settings(
        ctx: Context,
        voice_reference_id: str | None = None,
        persona_name: str | None = None,
        default_language: str | None = None,
        llm_model: str | None = None,
        max_minutes: int | None = None,
        monthly_budget_eur: float | None = None,
        quiet_hours: str | None = None,
        allowlist: list[str] | None = None,
        allowlist_enabled: bool | None = None,
        international_enabled: bool | None = None,
        mobile_flat_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Change call settings. Allowlist, international, and mobile-flat edits are refused.

        Those widen who can be called and are changed on the host with the CLI, not with
        this token. max_minutes and monthly_budget_eur cannot go above the env ceilings.
        The spoken AI disclosure is fixed per call language and cannot be changed here.
        """
        require_guide(ctx)
        patch = {
            key: value
            for key, value in {
                "voice_reference_id": voice_reference_id,
                "persona_name": persona_name,
                "default_language": default_language,
                "llm_model": llm_model,
                "max_minutes": max_minutes,
                "monthly_budget_eur": monthly_budget_eur,
                "quiet_hours": quiet_hours,
                "allowlist": allowlist,
                "allowlist_enabled": allowlist_enabled,
                "international_enabled": international_enabled,
                "mobile_flat_enabled": mobile_flat_enabled,
            }.items()
            if value is not None
        }
        try:
            return apply_mcp_settings(runtime.settings, patch)
        except Exception as exc:
            from fernruf.service.settings_update import SettingsUpdateError

            if isinstance(exc, SettingsUpdateError):
                _tool_error(exc.code, exc.message)
            raise

    @mcp.tool()
    async def place_call(
        to: str,
        task: CallTaskIn,
        ctx: Context,
        persona: str | None = None,
        language: str | None = None,
        max_minutes: int | None = None,
        record: bool = False,
        dtmf_prefix: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Queue one call and return immediately. Confirm the number and goal with the operator first.

        `language` (for example `en`) forces the call language and overrides detection.
        Omit it and the language is detected from `goal`, `context`, and `must_say`,
        not from the `default_language` setting. `model` overrides the configured LLM
        for this call only. Marketing calls are rejected.
        """
        require_guide(ctx)
        try:
            return await runtime.manager.place(
                to=to,
                task=task.as_dict(),
                persona=persona,
                language=language,
                max_minutes=max_minutes,
                record=record,
                dtmf_prefix=dtmf_prefix,
                model=model,
                wait=False,
            )
        except CallError as exc:
            _raise_call(exc)
            raise AssertionError("unreachable")

    @mcp.tool()
    async def place_call_and_wait(
        to: str,
        task: CallTaskIn,
        ctx: Context,
        persona: str | None = None,
        language: str | None = None,
        max_minutes: int | None = None,
        record: bool = False,
        dtmf_prefix: str | None = None,
        model: str | None = None,
        wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Place a call and keep this request open until it ends or wait_seconds elapses.

        `language` (for example `en`) forces the call language and overrides detection.
        Omit it and the language is detected from `goal`, `context`, and `must_say`,
        not from the `default_language` setting. Progress notifications are sent for
        the whole wait, up to the call's max minutes. The default wait is the full
        call. The result is the get_call snapshot.
        """
        require_guide(ctx)

        async def report(current: float, total: float, message: str) -> None:
            await ctx.report_progress(current, total, message)

        try:
            return await runtime.manager.place(
                to=to,
                task=task.as_dict(),
                persona=persona,
                language=language,
                max_minutes=max_minutes,
                record=record,
                dtmf_prefix=dtmf_prefix,
                model=model,
                wait=True,
                progress=report,
                wait_seconds=wait_seconds,
            )
        except CallError as exc:
            _raise_call(exc)
            raise AssertionError("unreachable")

    @mcp.tool()
    def get_call(call_id: str) -> dict[str, Any]:
        """Status, timings, outcome, transcript, and cost. Summary is null until a later version."""
        try:
            return runtime.manager.get_call(call_id)
        except CallError as exc:
            _raise_call(exc)
            raise AssertionError("unreachable")

    @mcp.tool()
    def get_call_transcript(call_id: str) -> dict[str, Any]:
        """Transcript lines for one call. complete is true once the call is finalized."""
        try:
            view = runtime.manager.get_call(call_id)
        except CallError as exc:
            _raise_call(exc)
            raise AssertionError("unreachable")
        return view["transcript"]

    @mcp.tool()
    def list_calls(limit: int = 20, since: str | None = None) -> list[dict[str, Any]]:
        """Recent calls. since is an ISO timestamp. summary fields are null until summaries exist."""
        try:
            return runtime.manager.list_calls(limit=limit, since=since)
        except ValueError as exc:
            _tool_error("invalid_settings", str(exc))
            raise AssertionError("unreachable")

    @mcp.tool()
    def get_recording(call_id: str) -> dict[str, Any]:
        """Recording link. Not available when nothing was recorded or consent was refused."""
        try:
            runtime.manager.get_call(call_id)
        except CallError as exc:
            _raise_call(exc)
            raise AssertionError("unreachable")
        return {"url": None, "expires_at": None, "status": "not_available"}

    @mcp.tool()
    async def hangup_call(call_id: str, ctx: Context) -> dict[str, Any]:
        """Hang up a queued or live call."""
        require_guide(ctx)
        try:
            return await runtime.manager.hangup(call_id)
        except CallError as exc:
            _raise_call(exc)
            raise AssertionError("unreachable")

    @mcp.tool()
    async def connect_webhook(
        url: str,
        ctx: Context,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        """Store a webhook URL and send a test event. Signed call.finalized delivery is later.

        The test body is only `{event: webhook.test}`. Call content is not posted yet.
        """
        require_guide(ctx)
        problem = webhook_url_error(url)
        if problem:
            _tool_error("webhook_url_rejected", problem)
        chosen = list(events) if events else list(WEBHOOK_EVENTS)
        unknown = [item for item in chosen if item not in WEBHOOK_EVENTS]
        if unknown:
            _tool_error("invalid_settings", "Unknown webhook event: " + ", ".join(unknown))
        webhook_id = uuid.uuid4().hex[:12]
        body = {
            "event": "webhook.test",
            "at": datetime.now(tz=timezone.utc).isoformat(),
            "data": {"ok": True},
        }
        try:
            delivered = await runtime.poster(url, body)
        except Exception:
            LOG.info("webhook test delivery failed id=%s", webhook_id)
            delivered = False
        save_webhook(
            runtime.settings,
            webhook_id=webhook_id,
            url=url,
            events=chosen,
            created_at=body["at"],
        )
        from fernruf.audit import append_audit_event

        host = urlparse(url).hostname or ""
        append_audit_event(
            Path(runtime.settings.audit_path),
            {"event": "connect_webhook", "webhook_id": webhook_id, "host": host},
        )
        return {"webhook_id": webhook_id, "test_delivery": "ok" if delivered else "error"}

    return mcp


_OAUTH_PATHS = frozenset(
    {
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/register",
        "/authorize",
        "/token",
        "/oauth/github/callback",
    }
)
_RATE_LIMITED = frozenset({"/register", "/authorize", "/token"})


def _unauthorized(metadata_url: str) -> JSONResponse:
    header = f'Bearer error="invalid_token", resource_metadata="{metadata_url}"'
    return JSONResponse(
        {"error": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": header},
    )


def _split_bearer(header: str) -> tuple[str | None, str]:
    if not header:
        return None, "missing_bearer"
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        return None, "missing_bearer"
    return rest.strip(), ""


def _bearer_ok(presented: str, runtime: ServiceRuntime) -> bool:
    if runtime.token and bearer_matches(presented, runtime.token):
        return True
    return runtime.store.access_grant(presented) is not None


async def _protect(request: Request, call_next, runtime: ServiceRuntime):
    path = request.url.path
    if path in ("/healthz", "/healthz/"):
        return JSONResponse({"ok": True})
    if path in _OAUTH_PATHS:
        if path in _RATE_LIMITED and request.method != "OPTIONS":
            ip = client_ip(request, runtime.settings.trusted_proxy_set)
            if not runtime.oauth_limiter.allow(f"{ip}:{path}"):
                return JSONResponse(
                    {"error": "rate_limited"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)
    if path != "/mcp" and not path.startswith("/mcp/"):
        return JSONResponse({"error": "not_found"}, status_code=404)
    ip = client_ip(request, runtime.settings.trusted_proxy_set)
    if runtime.lockout.locked(ip):
        AUTH_LOG.warning(AUTH_LOG_LINE, ip, "lockout")
        return JSONResponse(
            {"error": "rate_limited"},
            status_code=429,
            headers={"Retry-After": str(runtime.lockout.retry_after_s(ip))},
        )
    presented, reason = _split_bearer(request.headers.get("authorization", ""))
    if presented is None or not _bearer_ok(presented, runtime):
        runtime.lockout.record_failure(ip)
        AUTH_LOG.warning(AUTH_LOG_LINE, ip, reason or "bad_token")
        return _unauthorized(runtime.resource_metadata_url)
    runtime.lockout.record_success(ip)
    return await call_next(request)


def create_app(
    settings: Settings,
    *,
    manager: CallManager | None = None,
    lockout: AuthLockout | None = None,
    line_health: LineHealth | None = None,
    poster=None,
    runner=None,
    now=None,
    keepalive_s: float = 5.0,
    github: GitHubLogin | None = None,
) -> FastAPI:
    """HTTP app. Refuses to build when no login method is configured, or the static token is weak."""
    health = line_health or line_health_for(settings)
    call_manager = manager or CallManager(
        settings,
        line_health=health,
        runner=runner,
        now=now,
        keepalive_s=keepalive_s,
    )
    auth_lock = lockout or AuthLockout(
        max_failures=settings.auth_fail_max,
        window_s=settings.auth_fail_window_s,
        lockout_s=settings.auth_lockout_s,
    )
    runtime = ServiceRuntime(
        settings,
        call_manager,
        auth_lock,
        poster or _default_poster,
    )
    runtime.store.ensure()
    get_call_settings(settings)
    github_login = github or GitHubLogin(settings)
    provider = FernrufOAuthProvider(settings, runtime.store, github_login)
    mcp = build_mcp(runtime)
    allowed_hosts = mcp_allowed_hosts(settings)
    mcp_asgi = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[],
        ),
        json_response=False,
        session_idle_timeout=7200,
        max_request_body_size=_MAX_BODY,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="fernruf",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    issuer = (settings.oauth_issuer or "").strip()
    resource = (settings.oauth_resource or "").strip()
    if issuer and resource:
        for route in reversed(
            oauth_routes(settings, runtime.store, provider, github_login)
        ):
            app.router.routes.insert(0, route)
    elif issuer or resource:
        LOG.warning(
            "OAuth partially configured (need both FERNRUF_OAUTH_ISSUER and "
            "FERNRUF_OAUTH_RESOURCE); OAuth routes not mounted."
        )
    app.mount("/", mcp_asgi)

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        return await _protect(request, call_next, runtime)

    app.state.runtime = runtime
    app.state.mcp = mcp
    return app
