from __future__ import annotations

import httpx

from fernruf.settings import Settings


class AriUnreachableError(RuntimeError):
    pass


def check_ari_reachable(settings: Settings, *, timeout_s: float = 3.0) -> None:
    """Fail fast when Asterisk ARI is not accepting connections."""
    url = settings.asterisk_ari_url.rstrip("/") + "/ari/asterisk/info"
    try:
        r = httpx.get(
            url,
            auth=(settings.asterisk_ari_user, settings.asterisk_ari_password),
            timeout=timeout_s,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        host = settings.asterisk_ari_url
        raise AriUnreachableError(
            f"Asterisk not running or ARI unreachable at {host}. "
            "Start production Asterisk with: "
            "sudo docker compose --env-file .env up -d asterisk"
        ) from exc
    if r.status_code == 401:
        raise AriUnreachableError(
            "Asterisk ARI rejected credentials (HTTP 401). "
            "Check ASTERISK_ARI_USER / ASTERISK_ARI_PASSWORD in your env file."
        )
    if r.status_code >= 400:
        raise AriUnreachableError(
            f"Asterisk ARI error HTTP {r.status_code} from {url}: {(r.text or '')[:200]}"
        )
