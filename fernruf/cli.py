from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click
import httpx

from fernruf.ari.client import AriSession
from fernruf.ari.m2 import M2AriSession
from fernruf.audit import append_call_audit
from fernruf.dial import e164_to_sip_dial, normalize_e164
from fernruf.logging_config import collect_secret_literals, configure_logging
from fernruf.policy import PolicyDecision, evaluate_call_policy
from fernruf.settings import load_settings, validate_ari_password
from fernruf.tts.audio import prepare_m1_prompt

LOG = logging.getLogger("fernruf")


@click.group()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    settings = load_settings()
    configure_logging(verbose, secrets=collect_secret_literals(settings))


@main.command("policy-check")
@click.argument("to")
@click.option("--at-iso", default=None, help="ISO timestamp for quiet-hours tests")
def policy_check(to: str, at_iso: str | None) -> None:
    settings = load_settings()
    now = None
    if at_iso:
        from datetime import datetime

        from fernruf.quiet_hours import BERLIN

        now = datetime.fromisoformat(at_iso)
        if now.tzinfo is None:
            now = now.replace(tzinfo=BERLIN)
    try:
        e164 = normalize_e164(to)
    except ValueError:
        e164 = to
    qh = settings.quiet_hours_spec
    decision = evaluate_call_policy(
        e164,
        allowlist_enabled=True,
        allowlist=settings.allowlist_numbers,
        international_enabled=settings.international_enabled,
        max_minutes=settings.max_minutes,
        quiet_hours=qh,
        now=now,
        raw_input=to,
    )
    click.echo(f"quiet_hours: {decision.quiet_hours}")
    if decision.allowed:
        click.echo(f"ALLOW {decision.reason or ''}".strip())
        raise SystemExit(0)
    click.echo(f"REJECT {decision.reason}")
    raise SystemExit(2)


@main.command("m1-test-call")
@click.option("--to", required=True, help="Callee E.164, e.g. +49151…")
@click.option("--min-hold", default=32, show_default=True, help="Seconds to keep call up after answer")
@click.option("--dry-run", is_flag=True, help="Policy + dial format only, no ARI")
def m1_test_call(to: str, min_hold: int, dry_run: bool) -> None:
    settings = load_settings(require_principal=True)
    validate_ari_password(settings.asterisk_ari_password)
    e164 = normalize_e164(to)
    qh = settings.quiet_hours_spec
    decision = evaluate_call_policy(
        e164,
        allowlist_enabled=True,
        allowlist=settings.allowlist_numbers,
        international_enabled=settings.international_enabled,
        max_minutes=settings.max_minutes,
        quiet_hours=qh,
    )
    append_call_audit(
        Path(settings.audit_path),
        to_e164=e164,
        decision=decision,
        quiet_hours=decision.quiet_hours,
    )
    if not decision.allowed:
        LOG.error("policy rejected: %s", decision.reason)
        raise SystemExit(2)

    from fernruf.principal import dial_blocked_placeholder_reason

    blocked = dial_blocked_placeholder_reason(settings)
    if blocked:
        LOG.error("%s", blocked)
        raise SystemExit(2)

    sip_dial = e164_to_sip_dial(e164)
    LOG.info("originate %s -> %s callerId %s", e164, sip_dial, settings.ari_caller_id)

    if dry_run:
        click.echo(json_summary(decision, sip_dial, None))
        return

    from fernruf.ari.health import AriUnreachableError, check_ari_reachable

    try:
        check_ari_reachable(settings)
    except AriUnreachableError as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from None

    source, play_media = prepare_m1_prompt(settings)
    LOG.info("prompt audio: %s (%s)", source, play_media)

    endpoint = f"PJSIP/{sip_dial}@sip-trunk"
    ari = AriSession(
        base_url=settings.asterisk_ari_url,
        user=settings.asterisk_ari_user,
        password=settings.asterisk_ari_password,
    )
    result: dict | None = None
    try:
        try:
            result = asyncio.run(
                ari.run_stasis_call(
                    endpoint,
                    caller_id=settings.ari_caller_id,
                    play_media=play_media,
                    min_hold_s=float(min_hold),
                )
            )
        except httpx.HTTPStatusError as exc:
            LOG.error("%s", exc)
            raise SystemExit(1) from None
        except KeyboardInterrupt:
            LOG.warning("interrupted; hanging up active channel")
            ari.hangup_active()
            raise SystemExit(130) from None
    finally:
        ari.close()

    append_call_audit(
        Path(settings.audit_path),
        to_e164=e164,
        decision=decision,
        quiet_hours=decision.quiet_hours,
        extra={
            "event": "m1_test_call_completed",
            "answered": result["answered"] if result else False,
            "hold_s": result["hold_s"] if result else 0,
            "dtmf": result["dtmf"] if result else [],
        },
    )

    if not result or not result["answered"]:
        LOG.error("call never answered")
        raise SystemExit(1)
    if result["hold_s"] < min_hold - 0.5:
        LOG.error("hold too short: %.1fs (need %.1fs)", result["hold_s"], min_hold)
        raise SystemExit(1)

    LOG.info("DTMF received: %s", "".join(result["dtmf"]) or "(none)")
    click.echo(json_summary(decision, sip_dial, result))


@main.command("m2-test-call")
@click.option("--to", required=True, help="Callee E.164")
@click.option("--task", required=True, help="Brief / goal for the agent")
@click.option("--max-minutes", default=None, type=int, help="Override policy max minutes")
@click.option("--simulate", is_flag=True, help="Offline conversation (CI / no keys)")
@click.option("--dry-run", is_flag=True, help="Policy + dial format only")
@click.option(
    "--model",
    default=None,
    help="Per-call LLM model override (OpenAI-compatible model id); else persisted then env",
)
def m2_test_call(
    to: str,
    task: str,
    max_minutes: int | None,
    simulate: bool,
    dry_run: bool,
    model: str | None,
) -> None:
    if model is not None:
        from fernruf.call_settings.model import validate_llm_model_id

        try:
            model = validate_llm_model_id(model)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    settings = load_settings(require_principal=True)
    from fernruf.principal import dial_blocked_placeholder_reason

    if not dry_run and not simulate and not settings.voice_mock:
        blocked = dial_blocked_placeholder_reason(settings)
        if blocked:
            LOG.error("%s", blocked)
            raise SystemExit(2)
    validate_ari_password(settings.asterisk_ari_password)
    e164 = normalize_e164(to)
    qh = settings.quiet_hours_spec
    decision = evaluate_call_policy(
        e164,
        allowlist_enabled=True,
        allowlist=settings.allowlist_numbers,
        international_enabled=settings.international_enabled,
        max_minutes=max_minutes or settings.max_minutes,
        quiet_hours=qh,
    )
    append_call_audit(
        Path(settings.audit_path),
        to_e164=e164,
        decision=decision,
        quiet_hours=decision.quiet_hours,
        extra={"event": "m2_test_call_started", "task": task[:200]},
    )
    if not decision.allowed:
        LOG.error("policy rejected: %s", decision.reason)
        raise SystemExit(2)

    sip_dial = e164_to_sip_dial(e164)
    if dry_run:
        click.echo(json_summary(decision, sip_dial, {"mode": "dry-run", "task": task}))
        return

    from fernruf.call_settings.resolver import llm_model_missing_reason

    if not simulate and not settings.voice_mock:
        missing_model = llm_model_missing_reason(settings)
        if missing_model:
            LOG.error("%s", missing_model)
            raise SystemExit(2)

    if not simulate:
        from fernruf.ari.health import AriUnreachableError, check_ari_reachable

        try:
            check_ari_reachable(settings)
        except AriUnreachableError as exc:
            LOG.error("%s", exc)
            raise SystemExit(1) from None

    if simulate or settings.voice_mock:
        missing_model = llm_model_missing_reason(settings)
        if missing_model:
            LOG.error("%s", missing_model)
            raise SystemExit(2)
        from fernruf.voice.simulate import SimulateConversation

        result = asyncio.run(
            SimulateConversation(settings, task, llm_model_override=model).run()
        )
        payload = {
            "mode": "simulate",
            "llm_model": result.llm_model,
            "llm_model_source": result.llm_model_source,
            "llm_provider": result.llm_provider,
            "first_line": result.transcript[0].text if result.transcript else "",
            "lines": [
                {"t_ms": ln.t_ms, "speaker": ln.speaker, "text": ln.text}
                for ln in result.transcript
            ],
            "median_latency": result.median,
            "latency_simulated": True,
            "latency_log": str(result.latency_path),
            "call_id": result.call_id,
            "call_language": result.call_language,
        }
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    from fernruf.call_settings.resolver import resolve_call_llm_model
    from fernruf.llm.preflight import LlmPreflightError, preflight_llm
    from fernruf.voice.call_session import M2CallSession
    from fernruf.voice.hooks import M2CallHooks
    from fernruf.voice.m2_pipeline import _llm_tools
    from fernruf.voice.prompt import build_system_prompt

    async def _noop() -> None:
        return None

    from fernruf.language import prepare_call_settings

    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=_noop, hangup=_noop, session=session)

    async def _boot():
        prepared = await prepare_call_settings(settings, task)
        system_prompt = build_system_prompt(prepared, task)
        preflight_tools = [] if prepared.llm_mock else _llm_tools(hooks, session, prepared)
        resolved_model = resolve_call_llm_model(prepared, override=model)
        preflight_result = await preflight_llm(
            prepared,
            model=resolved_model.model,
            system_instruction=system_prompt,
            tools=preflight_tools,
        )
        return prepared, preflight_result

    call_llm_model = model
    try:
        settings, preflight_result = asyncio.run(_boot())
        llm_reasoning_override = preflight_result.reasoning_override
        if preflight_result.model_override:
            call_llm_model = preflight_result.model_override
    except LlmPreflightError as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from None

    endpoint = f"PJSIP/{sip_dial}@sip-trunk"
    ari = M2AriSession(
        base_url=settings.asterisk_ari_url,
        user=settings.asterisk_ari_user,
        password=settings.asterisk_ari_password,
    )
    from fernruf.ari.m2 import run_m2_test_call_with_signals

    try:
        out = asyncio.run(
            run_m2_test_call_with_signals(
                ari,
                endpoint=endpoint,
                caller_id=settings.ari_caller_id,
                settings=settings,
                task=task,
                max_minutes=max_minutes or settings.max_minutes,
                llm_model_override=call_llm_model,
                llm_reasoning_override=llm_reasoning_override,
                skip_llm_preflight=True,
            )
        )
    finally:
        ari.close()
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@main.group("settings")
def settings_group() -> None:
    """Persisted call settings (M3 get_call_settings / M5 UI)."""


@settings_group.command("show")
def settings_show() -> None:
    from fernruf.call_settings.store import get_call_settings

    rec = get_call_settings()
    click.echo(json.dumps(rec.to_public_dict(), indent=2, ensure_ascii=False))


@main.command("stt-smoke")
@click.option(
    "--wav",
    "wav_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Mono 16-bit WAV (default: bundled German sample)",
)
def stt_smoke(wav_path: str | None) -> None:
    """Transcribe a short WAV with faster-whisper (validates CUDA preload)."""
    import time
    from pathlib import Path

    from fernruf.stt.whisper import (
        SttPreloadError,
        active_stt_device,
        prepare_stt_for_call,
        transcribe_wav_file,
    )

    try:
        settings = load_settings()
    except SystemExit as exc:
        raise SystemExit(str(exc)) from None
    if settings.voice_mock:
        raise SystemExit("FERNRUF_VOICE_MOCK is enabled; disable for stt-smoke")
    path = Path(wav_path) if wav_path else Path(__file__).resolve().parents[1] / "tests/fixtures/stt_smoke_de.wav"
    if not path.is_file():
        raise SystemExit(f"Missing STT smoke WAV: {path}")
    try:
        prepare_stt_for_call(settings)
    except SttPreloadError as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from None
    t0 = time.perf_counter()
    seg = transcribe_wav_file(str(path), settings=settings)
    elapsed = time.perf_counter() - t0
    click.echo(
        json.dumps(
            {
                "device": active_stt_device(),
                "text": seg.text,
                "language": seg.language,
                "elapsed_s": round(elapsed, 3),
                "wav": str(path),
                "model": settings.whisper_model,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    if not seg.text.strip():
        LOG.warning("STT returned empty text (CUDA path may still be OK)")


@main.command("fish-smoke")
@click.argument("text", default="Hallo, dies ist ein Fish-Audio-Test.")
def fish_smoke(text: str) -> None:
    """Synthesize one sentence via Fish REST TTS (live API key required)."""
    from fernruf.tts.fish import FishTtsError
    from fernruf.tts.fish_rest_client import astream_fish_tts_pcm_rest

    settings = load_settings()
    if not settings.fish_audio_api_key.strip():
        raise SystemExit("FISH_AUDIO_API_KEY is not set")

    async def _run() -> tuple[bytes, int, float | None]:
        chunks: list[bytes] = []
        ttfa_ms: float | None = None
        async for chunk, first_ttfa in astream_fish_tts_pcm_rest(
            text,
            settings=settings,
            api_key=settings.fish_audio_api_key,
            model=settings.fish_audio_model,
            voice_id=settings.fish_voice_id or None,
        ):
            if first_ttfa is not None:
                ttfa_ms = first_ttfa
            chunks.append(chunk)
        pcm = b"".join(chunks)
        return pcm, 16_000, ttfa_ms

    try:
        pcm, rate, ttfa_ms = asyncio.run(_run())
    except FishTtsError as exc:
        LOG.error("Fish TTS failed: %s", exc)
        raise SystemExit(1) from None
    rate = 16_000
    duration_s = len(pcm) / (2 * rate) if rate else 0.0
    click.echo(
        json.dumps(
            {
                "bytes": len(pcm),
                "sample_rate": rate,
                "duration_s": round(duration_s, 3),
                "ttfa_ms": round(ttfa_ms, 1) if ttfa_ms is not None else None,
                "model": settings.fish_audio_model,
                "voice_id": settings.fish_voice_id or None,
                "fish_latency": settings.fish_latency,
                "fish_chunk_length": settings.fish_chunk_length,
            },
            indent=2,
        )
    )


@main.command("mood-smoke")
@click.argument("text", nargs=-1)
def mood_smoke(text: tuple[str, ...]) -> None:
    """Score German lines with TypeSafe Jev. Prints choice, confidence, and latency."""
    from fernruf.mood import JevMoodClient, fish_tag_for

    settings = load_settings()
    if not settings.typesafe_api_key.strip():
        raise SystemExit("TYPESAFE_API_KEY is not set")
    samples = list(text) or [
        "Ja super, das freut mich wirklich!",
        "Hmm, ich weiß nicht, ob ich Ihnen das glauben soll.",
        "Das ist jetzt gerade wirklich ungünstig, ich habe überhaupt keine Zeit.",
        "Haha, das ist ja lustig.",
        "Ach, das ist schade.",
        "Mhm, okay.",
        "Können Sie das bitte noch einmal sagen? Ich bin gerade etwas gestresst.",
    ]

    async def _run() -> list[dict[str, object]]:
        client = JevMoodClient(settings)
        rows: list[dict[str, object]] = []
        for line in samples:
            reading = await client.detect(line)
            tag = fish_tag_for(
                reading.choice,
                reading.confidence,
                min_confidence=settings.mood_min_confidence,
                fish_model=settings.effective_fish_model(),
            )
            rows.append(
                {
                    "text": line,
                    "choice": reading.choice,
                    "confidence": reading.confidence,
                    "latency_ms": None
                    if reading.latency_ms is None
                    else round(reading.latency_ms, 1),
                    "tag": tag or None,
                }
            )
        return rows

    click.echo(json.dumps(asyncio.run(_run()), indent=2, ensure_ascii=False))


@main.command("m2-verify-loopback")
@click.option("--to", required=True, help="Loopback callee E.164 (SIPp)")
@click.option(
    "--task",
    default="Kurzer Verifikationsanruf. Bestätige, dass du mich hörst.",
    show_default=True,
)
def m2_verify_loopback(to: str, task: str) -> None:
    from fernruf.principal import dial_blocked_placeholder_reason
    from fernruf.verify.m2_loopback import run_m2_verify_loopback

    settings = load_settings(require_principal=True)
    validate_ari_password(settings.asterisk_ari_password)
    blocked = dial_blocked_placeholder_reason(settings)
    if blocked:
        LOG.error("%s", blocked)
        raise SystemExit(2)
    e164 = normalize_e164(to)
    endpoint = f"PJSIP/{e164_to_sip_dial(e164)}@sip-trunk"
    result = asyncio.run(
        run_m2_verify_loopback(
            settings,
            endpoint=endpoint,
            caller_id=settings.ari_caller_id,
            task=task,
        )
    )
    click.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    if not result.ok:
        for err in result.errors:
            LOG.error("verify: %s", err)
        raise SystemExit(1)


@main.command("gen-token")
def gen_token() -> None:
    """Print a new FERNRUF_MCP_TOKEN (32 random bytes). Nothing is stored."""
    from fernruf.service.security import generate_mcp_token

    click.echo(generate_mcp_token())


@main.command("serve")
def serve() -> None:
    """MCP service (default bind 127.0.0.1:8787; often reached via a private tunnel/VPN)."""
    import uvicorn

    from fernruf.service.mcp_app import create_app
    from fernruf.service.security import (
        AuthConfigError,
        BindError,
        McpTokenError,
        github_login_configured,
        require_login_configured,
        validate_bind_host,
    )
    from fernruf.service.startup import FernrufServer

    settings = load_settings(require_principal=True)
    try:
        static = require_login_configured(
            settings.mcp_token,
            settings.github_client_id,
            settings.github_client_secret,
        )
        host = validate_bind_host(settings.bind_host, override=settings.bind_override)
    except (McpTokenError, BindError, AuthConfigError) as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from None
    if settings.bind_port == 8080:
        LOG.warning("bind port 8080 is often used by other services; fernruf defaults to 8787")
    configure_logging(False, secrets=collect_secret_literals(settings))
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=host,
        port=settings.bind_port,
        access_log=False,
        proxy_headers=False,
        forwarded_allow_ips="",
        log_level="info",
    )
    FernrufServer(
        config,
        settings,
        github=github_login_configured(settings.github_client_id, settings.github_client_secret),
        static_token=static is not None,
    ).run()


@main.group("auth")
def auth_group() -> None:
    """OAuth grants stored on the host. Tokens are not printed."""


@auth_group.command("list")
def auth_list() -> None:
    """List active OAuth grants. No tokens, secrets, or phone numbers."""
    from fernruf.service.oauth_store import OAuthStore

    click.echo(json.dumps(OAuthStore(load_settings()).list_active_grants(), indent=2))


@auth_group.command("revoke-all")
def auth_revoke_all() -> None:
    """Revoke every OAuth grant. The connector must sign in with GitHub again."""
    from fernruf.service.oauth_store import OAuthStore

    click.echo(f"revoked {OAuthStore(load_settings()).revoke_all()}")


@settings_group.command("allow-number")
@click.argument("number")
def settings_allow_number(number: str) -> None:
    """Add an E.164 number to the allowlist. This is the only way to widen it."""
    from fernruf.call_settings.store import get_call_settings, update_call_settings
    from fernruf.audit import append_audit_event

    settings = load_settings()
    try:
        e164 = normalize_e164(number)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    current = list(get_call_settings(settings).allowlist)
    if e164 not in current:
        current.append(e164)
    update_call_settings(
        {"allowlist": current, "allowlist_enabled": True},
        settings=settings,
    )
    append_audit_event(
        Path(settings.audit_path),
        {"event": "allowlist_add", "number": e164},
    )
    LOG.info("fernruf allowlist add number=%s", e164)
    click.echo(e164)


@settings_group.command("disallow-number")
@click.argument("number")
def settings_disallow_number(number: str) -> None:
    """Remove an E.164 number from the allowlist."""
    from fernruf.audit import append_audit_event
    from fernruf.call_settings.store import get_call_settings, update_call_settings

    settings = load_settings()
    try:
        e164 = normalize_e164(number)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    current = [item for item in get_call_settings(settings).allowlist if item != e164]
    update_call_settings({"allowlist": current}, settings=settings)
    append_audit_event(
        Path(settings.audit_path),
        {"event": "allowlist_remove", "number": e164},
    )
    LOG.info("fernruf allowlist remove number=%s", e164)
    click.echo(e164)


@settings_group.command("set-llm-model")
@click.argument("model_id")
def settings_set_llm_model(model_id: str) -> None:
    from fernruf.call_settings.store import update_call_settings

    rec = update_call_settings({"llm_model": model_id})
    click.echo(f"llm_model={rec.llm_model} (applies to next call, no restart)")


def json_summary(decision: PolicyDecision, sip_dial: str, result: dict | None) -> str:
    import json

    payload = {
        "quiet_hours": decision.quiet_hours,
        "policy": decision.outcome.value,
        "sip_dial": sip_dial,
        "call": result,
    }
    return json.dumps(payload, indent=2)


if __name__ == "__main__":
    main(standalone_mode=True)
