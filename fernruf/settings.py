from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fernruf.principal import german_ai_notice, require_principal_name
from fernruf.quiet_hours import QuietHoursSpec, parse_quiet_hours
from fernruf.safety_limits import (
    clamp_calls_per_day,
    clamp_minutes_per_day,
    clamp_monthly_budget_eur,
)

def _configured_env_value(key: str) -> str:
    direct = os.environ.get(key, "").strip()
    if direct:
        return direct
    override = os.environ.get("FERNRUF_ENV_FILE")
    if override is not None and override.strip().lower() in ("", "none", "off"):
        return ""
    path = Path(override) if override else Path(".env")
    if not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return ""
    return str(dotenv_values(path).get(key) or "").strip()


ARI_PASSWORD_PLACEHOLDERS = frozenset(
    {
        "",
        "change-me-ari-secret",
        "fernruf-dev",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    sip_host: str = Field(default="192.0.2.1", alias="SIP_HOST")
    sip_ip: str = Field(default="192.0.2.1", alias="SIP_IP")
    sip_port: int = Field(default=5060, alias="SIP_PORT")
    sip_user: str = Field(default="", alias="SIP_USER")
    sip_password: str = Field(default="", alias="SIP_PASSWORD")
    lan_cidr: str = Field(default="192.0.2.0/24", alias="LAN_CIDR")

    principal_name: str = Field(default="", alias="FERNRUF_PRINCIPAL_NAME")

    asterisk_ari_url: str = Field(default="http://127.0.0.1:8088", alias="ASTERISK_ARI_URL")
    asterisk_ari_user: str = Field(default="fernruf", alias="ASTERISK_ARI_USER")
    asterisk_ari_password: str = Field(default="", alias="ASTERISK_ARI_PASSWORD")

    caller_id_name: str = Field(default="Fernruf", alias="FERNRUF_CALLER_ID_NAME")
    caller_id_number: str = Field(default="100", alias="FERNRUF_CALLER_ID_NUMBER")

    allowlist: str = Field(default="", alias="FERNRUF_ALLOWLIST")
    international_enabled: bool = Field(default=False, alias="FERNRUF_INTERNATIONAL_ENABLED")
    max_minutes: int = Field(default=10, alias="FERNRUF_MAX_MINUTES")
    quiet_hours: str = Field(default="20:00-08:00", alias="FERNRUF_QUIET_HOURS")

    audit_path: str = Field(default="data/audit.jsonl", alias="FERNRUF_AUDIT_PATH")
    call_settings_db: str = Field(default="data/fernruf.sqlite", alias="FERNRUF_SETTINGS_DB")

    mcp_token: str = Field(default="", alias="FERNRUF_MCP_TOKEN")
    webhook_secret: str = Field(default="", alias="FERNRUF_WEBHOOK_SECRET")
    bind_host: str = Field(default="127.0.0.1", alias="FERNRUF_BIND_HOST")
    bind_port: int = Field(default=8787, alias="FERNRUF_BIND_PORT")
    bind_override: str = Field(default="", alias="FERNRUF_BIND_OVERRIDE")
    trusted_proxies: str = Field(default="", alias="FERNRUF_TRUSTED_PROXIES")
    allowed_mcp_hosts: str = Field(default="", alias="FERNRUF_ALLOWED_HOSTS")
    quiet_hours_allow_off: bool = Field(default=False, alias="FERNRUF_QUIET_HOURS_ALLOW_OFF")
    auth_fail_max: int = Field(default=5, alias="FERNRUF_AUTH_FAIL_MAX")
    auth_fail_window_s: int = Field(default=300, alias="FERNRUF_AUTH_FAIL_WINDOW_S")
    auth_lockout_s: int = Field(default=900, alias="FERNRUF_AUTH_LOCKOUT_S")
    calls_per_day: int = Field(default=10, alias="FERNRUF_CALLS_PER_DAY")
    minutes_per_day: int = Field(default=100, alias="FERNRUF_MINUTES_PER_DAY")
    monthly_budget_eur: float = Field(default=25.0, alias="FERNRUF_MONTHLY_BUDGET_EUR")
    mobile_budget_eur: float = Field(default=20.0, alias="FERNRUF_MOBILE_BUDGET_EUR", le=50.0, ge=0.0)
    mobile_flat_enabled: bool = Field(default=False, alias="FERNRUF_MOBILE_FLAT_ENABLED")
    max_minutes_cap: int = Field(default=30, alias="FERNRUF_MAX_MINUTES_CAP")
    ai_eur_per_min: float = Field(default=0.01, alias="FERNRUF_AI_EUR_PER_MIN", ge=0.001)
    mobile_eur_per_min: float = Field(default=0.199, alias="FERNRUF_MOBILE_EUR_PER_MIN")
    m3_simulate: bool = Field(default=False, alias="FERNRUF_M3_SIMULATE")

    # OAuth 2.1 authorization server. GitHub is the only login.
    github_client_id: str = Field(default="", alias="FERNRUF_GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="FERNRUF_GITHUB_CLIENT_SECRET")
    github_allowed_user_ids: str = Field(default="", alias="FERNRUF_GITHUB_ALLOWED_USER_IDS")
    github_callback_url: str = Field(default="", alias="FERNRUF_GITHUB_CALLBACK_URL")
    oauth_issuer: str = Field(default="", alias="FERNRUF_OAUTH_ISSUER")
    oauth_resource: str = Field(default="", alias="FERNRUF_OAUTH_RESOURCE")
    oauth_access_ttl_s: int = Field(default=3600, alias="FERNRUF_OAUTH_ACCESS_TTL_S")
    oauth_refresh_ttl_s: int = Field(default=90 * 24 * 3600, alias="FERNRUF_OAUTH_REFRESH_TTL_S")
    oauth_max_clients: int = Field(default=8, alias="FERNRUF_OAUTH_MAX_CLIENTS")
    oauth_rate_per_min: int = Field(default=10, alias="FERNRUF_OAUTH_RATE_PER_MIN")

    notify_enabled: bool = Field(default=False, alias="FERNRUF_NOTIFY_ENABLED")
    notify_smtp_host: str = Field(default="", alias="FERNRUF_NOTIFY_SMTP_HOST")
    notify_smtp_port: int = Field(default=587, alias="FERNRUF_NOTIFY_SMTP_PORT")
    notify_smtp_tls_name: str = Field(default="", alias="FERNRUF_NOTIFY_SMTP_TLS_NAME")
    notify_smtp_require_tls: bool = Field(default=True, alias="FERNRUF_NOTIFY_SMTP_REQUIRE_TLS")
    notify_smtp_ehlo: str = Field(default="", alias="FERNRUF_NOTIFY_SMTP_EHLO")
    notify_from: str = Field(default="", alias="FERNRUF_NOTIFY_FROM")
    notify_to: str = Field(default="", alias="FERNRUF_NOTIFY_TO")
    notify_timeout_s: float = Field(default=5.0, alias="FERNRUF_NOTIFY_TIMEOUT_S")

    @property
    def github_allowed_ids(self) -> frozenset[str]:
        return frozenset(
            part.strip() for part in self.github_allowed_user_ids.split(",") if part.strip()
        )

    @property
    def trusted_proxy_set(self) -> frozenset[str]:
        raw = (self.trusted_proxies or "").strip()
        if raw:
            return frozenset(p.strip() for p in raw.split(",") if p.strip())
        host = (self.bind_host or "").strip().lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return frozenset({"127.0.0.1", "::1"})
        return frozenset()

    fish_audio_api_key: str = Field(default="", alias="FISH_AUDIO_API_KEY")
    fish_audio_model: str = Field(default="s2.1-pro-free", alias="FISH_AUDIO_MODEL")
    fish_voice_id: str = Field(default="", alias="FISH_VOICE_ID")
    demo_caller_voice_id: str = Field(default="", alias="FERNRUF_DEMO_CALLER_VOICE_ID")
    demo_callee_voice_id: str = Field(default="", alias="FERNRUF_DEMO_CALLEE_VOICE_ID")
    fish_tts_use_websocket: bool = Field(default=False, alias="FISH_TTS_USE_WEBSOCKET")
    fish_tts_rest_fixture: str = Field(default="", alias="FERNRUF_FISH_TTS_REST_FIXTURE")
    fish_latency: str = Field(default="balanced", alias="FERNRUF_FISH_LATENCY")
    fish_chunk_length: int = Field(default=100, alias="FERNRUF_FISH_CHUNK_LENGTH")
    fish_first_clause: bool = Field(default=True, alias="FERNRUF_FISH_FIRST_CLAUSE")
    fish_transport: str = Field(default="rest", alias="FERNRUF_FISH_TRANSPORT")
    fish_model: str = Field(default="s2.1-pro-free", alias="FERNRUF_FISH_MODEL")

    @field_validator("fish_latency")
    @classmethod
    def _fish_latency_valid(cls, v: str) -> str:
        mode = (v or "balanced").strip().lower()
        if mode not in ("low", "balanced", "normal"):
            raise ValueError("FERNRUF_FISH_LATENCY must be low, balanced, or normal")
        return mode

    @field_validator("fish_chunk_length")
    @classmethod
    def _fish_chunk_length_valid(cls, v: int) -> int:
        if not 100 <= v <= 300:
            raise ValueError("FERNRUF_FISH_CHUNK_LENGTH must be between 100 and 300")
        return v

    @field_validator("fish_transport")
    @classmethod
    def _fish_transport_valid(cls, v: str) -> str:
        mode = (v or "rest").strip().lower()
        if mode not in ("rest", "websocket"):
            raise ValueError("FERNRUF_FISH_TRANSPORT must be rest or websocket")
        return mode

    def effective_fish_model(self) -> str:
        return (self.fish_model or self.fish_audio_model).strip()

    def fish_prefers_websocket(self) -> bool:
        if self.fish_tts_use_websocket:
            return True
        return self.fish_transport == "websocket"

    # TypeSafe Jev (api.typesafe.ai): opt-in. Off by default for public deployments.
    typesafe_api_key: str = Field(default="", alias="TYPESAFE_API_KEY")
    typesafe_enabled: bool = Field(default=False, alias="FERNRUF_TYPESAFE_ENABLED")
    privacy_minimal: bool = Field(default=False, alias="FERNRUF_PRIVACY_MINIMAL")
    # Callee mood (Jev). Off by default. Requires typesafe_requests_enabled().
    mood_enabled: bool = Field(default=False, alias="FERNRUF_MOOD_ENABLED")
    mood_model: str = Field(default="jev-latest", alias="FERNRUF_MOOD_MODEL")
    mood_timeout_ms: int = Field(default=300, alias="FERNRUF_MOOD_TIMEOUT_MS")
    mood_min_confidence: float = Field(default=0.9, alias="FERNRUF_MOOD_MIN_CONFIDENCE")

    @field_validator("mood_model")
    @classmethod
    def _mood_model_valid(cls, v: str) -> str:
        model = (v or "").strip()
        if not model:
            raise ValueError("FERNRUF_MOOD_MODEL must not be empty")
        return model

    @field_validator("mood_timeout_ms")
    @classmethod
    def _mood_timeout_valid(cls, v: int) -> int:
        if v < 0 or v > 10_000:
            raise ValueError("FERNRUF_MOOD_TIMEOUT_MS must be between 0 and 10000")
        return v

    @field_validator("mood_min_confidence")
    @classmethod
    def _mood_confidence_valid(cls, v: float) -> float:
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError("FERNRUF_MOOD_MIN_CONFIDENCE must be between 0 and 1")
        return float(v)

    # Call language: explicit ``language`` arg, else local heuristics / default_language,
    # or TypeSafe Jev when FERNRUF_TYPESAFE_ENABLED=true.
    language_model: str = Field(default="jev-latest", alias="FERNRUF_LANGUAGE_MODEL")
    language_timeout_ms: int = Field(default=2000, alias="FERNRUF_LANGUAGE_TIMEOUT_MS")
    language_min_confidence: float = Field(
        default=0.6,
        alias="FERNRUF_LANGUAGE_MIN_CONFIDENCE",
    )
    language_translate_timeout_s: float = Field(
        default=8.0,
        alias="FERNRUF_LANGUAGE_TRANSLATE_TIMEOUT_S",
    )
    # Set on the per-call settings copy. Not an operator override.
    call_language: str = "de"
    call_phrases: dict[str, str] = Field(default_factory=dict)

    @field_validator("language_model")
    @classmethod
    def _language_model_valid(cls, v: str) -> str:
        model = (v or "").strip()
        if not model:
            raise ValueError("FERNRUF_LANGUAGE_MODEL must not be empty")
        return model

    @field_validator("language_timeout_ms")
    @classmethod
    def _language_timeout_valid(cls, v: int) -> int:
        if v < 0 or v > 10_000:
            raise ValueError("FERNRUF_LANGUAGE_TIMEOUT_MS must be between 0 and 10000")
        return v

    @field_validator("language_min_confidence")
    @classmethod
    def _language_confidence_valid(cls, v: float) -> float:
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError("FERNRUF_LANGUAGE_MIN_CONFIDENCE must be between 0 and 1")
        return float(v)

    @field_validator("language_translate_timeout_s")
    @classmethod
    def _language_translate_timeout_valid(cls, v: float) -> float:
        if not 0.0 <= float(v) <= 60.0:
            raise ValueError("FERNRUF_LANGUAGE_TRANSLATE_TIMEOUT_S must be between 0 and 60")
        return float(v)

    llm_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="FERNRUF_LLM_BASE_URL",
    )
    llm_api_key: str = Field(default="", alias="FERNRUF_LLM_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    llm_model: str = Field(default="", alias="FERNRUF_LLM_MODEL")
    llm_fallback_model: str = Field(default="", alias="FERNRUF_LLM_FALLBACK_MODEL")
    llm_preflight_slow_first_token_s: float = Field(
        default=3.0,
        alias="FERNRUF_LLM_PREFLIGHT_SLOW_S",
    )
    llm_provider_order: str = Field(default="", alias="FERNRUF_LLM_PROVIDER_ORDER")
    llm_delay_apology_phrase: str = Field(
        default="Entschuldigung, ich habe gerade eine kleine Verzögerung.",
        alias="FERNRUF_LLM_DELAY_APOLOGY",
    )
    llm_http_referer: str = Field(default="", alias="FERNRUF_LLM_HTTP_REFERER")
    llm_x_title: str = Field(default="fernruf", alias="FERNRUF_LLM_X_TITLE")
    llm_mock: bool = Field(default=False, alias="FERNRUF_LLM_MOCK")
    llm_reasoning: str = Field(default="low", alias="FERNRUF_LLM_REASONING")
    llm_provider: str | None = Field(default=None, alias="FERNRUF_LLM_PROVIDER")

    @field_validator("llm_reasoning")
    @classmethod
    def _llm_reasoning_valid(cls, v: str) -> str:
        from fernruf.llm.extra import normalize_llm_reasoning

        normalize_llm_reasoning(v)
        return v.strip().lower()

    whisper_model: str = Field(default="base", alias="FERNRUF_WHISPER_MODEL")
    whisper_device: str = Field(
        default="auto",
        validation_alias=AliasChoices("FERNRUF_WHISPER_DEVICE", "FERNRUF_STT_DEVICE"),
    )
    whisper_compute_type: str = Field(default="int8", alias="FERNRUF_WHISPER_COMPUTE_TYPE")
    # Fallback language when detection fails or confidence is low.
    # A successful detection always drives Whisper; this value does not override it.
    whisper_language: str = Field(default="", alias="FERNRUF_WHISPER_LANGUAGE")
    # Drop a Whisper segment at or above this no_speech_prob. Default 0.6.
    whisper_no_speech_prob: float = Field(default=0.6, alias="FERNRUF_WHISPER_NO_SPEECH_PROB")
    # Drop a Whisper segment below this avg_logprob. Default -1.0.
    whisper_min_avg_logprob: float = Field(default=-1.0, alias="FERNRUF_WHISPER_MIN_AVG_LOGPROB")
    min_user_turns_before_hangup: int = Field(
        default=3,
        alias="FERNRUF_MIN_USER_TURNS_BEFORE_HANGUP",
    )
    min_call_duration_before_hangup_s: float = Field(
        default=45.0,
        alias="FERNRUF_MIN_CALL_DURATION_S",
    )

    ws_listen_host: str = Field(default="127.0.0.1", alias="FERNRUF_WS_HOST")
    ws_listen_port: int = Field(default=8765, alias="FERNRUF_WS_PORT")
    demo_callee_ws_port: int = Field(default=8766, alias="FERNRUF_DEMO_CALLEE_WS_PORT")

    latency_log_path: str = Field(default="data/latency.jsonl", alias="FERNRUF_LATENCY_LOG")
    silence_timeout_s: float = Field(default=20.0, alias="FERNRUF_SILENCE_TIMEOUT_S")
    llm_filler_delay_s: float = Field(default=1.2, alias="FERNRUF_LLM_FILLER_DELAY_S")
    llm_first_token_timeout_s: float = Field(
        default=4.0,
        alias="FERNRUF_LLM_FIRST_TOKEN_TIMEOUT_S",
    )
    hangup_margin_ms: int | None = Field(
        default=None,
        alias="FERNRUF_HANGUP_MARGIN_MS",
    )
    hangup_after_bot_stopped_margin_s: float = Field(
        default=0.7,
        alias="FERNRUF_HANGUP_AFTER_BOT_STOPPED_MARGIN_S",
    )
    hangup_playback_safety_timeout_s: float = Field(
        default=30.0,
        alias="FERNRUF_HANGUP_PLAYBACK_SAFETY_TIMEOUT_S",
    )
    hangup_no_audio_fallback_s: float = Field(
        default=2.0,
        alias="FERNRUF_HANGUP_NO_AUDIO_FALLBACK_S",
    )
    auto_hangup_after_goodbye_s: float = Field(
        default=3.0,
        alias="FERNRUF_AUTO_HANGUP_AFTER_GOODBYE_S",
    )
    filler_phrase: str = Field(default="Einen Moment…", alias="FERNRUF_FILLER_PHRASE")

    sounds_dir: str = Field(default="data/asterisk-sounds", alias="FERNRUF_SOUNDS_DIR")
    voice_mock: bool = Field(default=False, alias="FERNRUF_VOICE_MOCK")
    verify_stt_fixture: str = Field(
        default="Hallo, ich höre Sie gut.",
        alias="FERNRUF_VERIFY_STT_FIXTURE",
    )

    disclosure_prefix: str = Field(
        default="",
        description="Resolved spoken AI notice (fixed per-language template).",
    )
    spoken_privacy_enabled: bool = Field(
        default=False,
        alias="FERNRUF_SPOKEN_PRIVACY_ENABLED",
    )
    spoken_privacy_line: str = Field(
        default="",
        description="Resolved fixed privacy notice for the active call language.",
    )

    @property
    def allowlist_numbers(self) -> frozenset[str]:
        parts = [p.strip() for p in self.allowlist.split(",") if p.strip()]
        return frozenset(parts)

    @property
    def pjsip_callerid(self) -> str:
        return f'"{self.caller_id_name}" <{self.caller_id_number}>'

    @property
    def ari_caller_id(self) -> str:
        return self.pjsip_callerid

    @model_validator(mode="after")
    def _reject_legacy_spoken_overrides(self) -> Settings:
        if _configured_env_value("FERNRUF_DISCLOSURE_SUFFIX"):
            raise ValueError(
                "FERNRUF_DISCLOSURE_SUFFIX is not supported; the spoken AI notice is fixed."
            )
        if _configured_env_value("FERNRUF_SPOKEN_PRIVACY_LINE"):
            raise ValueError(
                "FERNRUF_SPOKEN_PRIVACY_LINE is not supported; "
                "use FERNRUF_SPOKEN_PRIVACY_ENABLED=true instead."
            )
        for key in ("TEST_SENTENCE", "FERNRUF_TEST_SENTENCE"):
            if _configured_env_value(key):
                raise ValueError(
                    f"{key} is not supported; the M1 test prompt uses a fixed sentence."
                )
        return self

    @model_validator(mode="after")
    def _validate_quiet_hours_spec(self) -> Settings:
        parse_quiet_hours(self.quiet_hours, allow_off=self.quiet_hours_allow_off)
        return self

    @model_validator(mode="after")
    def _apply_privacy_minimal(self) -> Settings:
        if self.privacy_minimal:
            object.__setattr__(self, "typesafe_enabled", False)
            object.__setattr__(self, "mood_enabled", False)
        return self

    @model_validator(mode="after")
    def _snapshot_operator_fish_voice(self) -> Settings:
        """Remember the loaded FISH_VOICE_ID. Language setup may replace the field."""
        if getattr(self, "_operator_fish_voice_id", None) is None:
            object.__setattr__(self, "_operator_fish_voice_id", (self.fish_voice_id or "").strip())
        return self

    def typesafe_requests_enabled(self) -> bool:
        """True when TypeSafe/Jev HTTP calls are allowed (enabled flag and API key)."""
        if self.privacy_minimal or not self.typesafe_enabled:
            return False
        return bool((self.typesafe_api_key or "").strip())

    @field_validator("max_minutes_cap")
    @classmethod
    def _max_minutes_cap(cls, v: int) -> int:
        if v < 1 or v > 30:
            raise ValueError("FERNRUF_MAX_MINUTES_CAP must be between 1 and 30")
        return v

    @field_validator("calls_per_day")
    @classmethod
    def _calls_per_day_cap(cls, v: int) -> int:
        return clamp_calls_per_day(v)

    @field_validator("minutes_per_day")
    @classmethod
    def _minutes_per_day_cap(cls, v: int) -> int:
        return clamp_minutes_per_day(v)

    @field_validator("monthly_budget_eur")
    @classmethod
    def _monthly_budget_cap(cls, v: float) -> float:
        return clamp_monthly_budget_eur(v)

    @field_validator("auth_fail_max")
    @classmethod
    def _auth_fail_max(cls, v: int) -> int:
        if v < 1:
            raise ValueError("FERNRUF_AUTH_FAIL_MAX must be at least 1")
        return v

    @property
    def quiet_hours_spec(self) -> QuietHoursSpec:
        return parse_quiet_hours(self.quiet_hours, allow_off=self.quiet_hours_allow_off)

    def require_principal(self) -> str:
        return require_principal_name(self.principal_name)

    def resolved_disclosure_prefix(self) -> str:
        from fernruf.disclosure import spoken_ai_disclosure

        return spoken_ai_disclosure(self)

    @property
    def hangup_margin_s(self) -> float:
        """Post-drain wait, and the fallback pad after the goodbye audio.

        ``FERNRUF_HANGUP_MARGIN_MS`` wins over the legacy seconds setting.
        Both cover Asterisk's jitter buffer and the PSTN/RTP path.
        """
        if self.hangup_margin_ms is not None:
            return max(0.0, self.hangup_margin_ms / 1000.0)
        return self.hangup_after_bot_stopped_margin_s

    @property
    def llm_api_key_resolved(self) -> str:
        if self.llm_api_key:
            return self.llm_api_key
        if "openrouter.ai" in self.llm_base_url and self.openrouter_api_key:
            return self.openrouter_api_key
        return self.openrouter_api_key

    @property
    def ws_listen_uri(self) -> str:
        return f"ws://{self.ws_listen_host}:{self.ws_listen_port}/media"

    @property
    def use_voice_mock(self) -> bool:
        """True when STT/TTS should use local fixtures instead of cloud APIs."""
        if self.voice_mock:
            return True
        return not self.fish_audio_api_key and not self.fish_tts_rest_fixture


def validate_ari_password(password: str) -> None:
    if password in ARI_PASSWORD_PLACEHOLDERS:
        raise SystemExit(
            "ASTERISK_ARI_PASSWORD is missing or still a placeholder. "
            "Generate one with: openssl rand -hex 16"
        )


def _settings_validation_exit(exc: Exception) -> None:
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        raise exc
    for err in exc.errors():
        loc = err.get("loc") or ()
        msg = str(err.get("msg") or exc)
        if msg.startswith("Value error, "):
            msg = msg.removeprefix("Value error, ")
        if loc:
            field = ".".join(str(part) for part in loc if str(part) not in ("__root__",))
            raise SystemExit(f"{field}: {msg}" if field else msg) from None
        raise SystemExit(msg) from None
    raise SystemExit(str(exc).splitlines()[0]) from None


def load_settings(*, require_principal: bool = False) -> Settings:
    """Load settings. Honors FERNRUF_ENV_FILE (path, or none/off to skip dotenv)."""
    override = os.environ.get("FERNRUF_ENV_FILE")
    from pydantic import ValidationError

    try:
        if override is not None and override.strip().lower() in ("", "none", "off"):
            settings = Settings(_env_file=None)
        elif override:
            settings = Settings(_env_file=override)
        else:
            settings = Settings()
    except ValidationError as exc:
        _settings_validation_exit(exc)
    if require_principal:
        validate_operational_settings(settings)
    return settings


def validate_operational_settings(settings: Settings) -> None:
    """Refuse to place calls or start the MCP service without a principal name."""
    try:
        settings.require_principal()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    from fernruf.disclosure import spoken_ai_disclosure

    try:
        object.__setattr__(settings, "disclosure_prefix", spoken_ai_disclosure(settings))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
