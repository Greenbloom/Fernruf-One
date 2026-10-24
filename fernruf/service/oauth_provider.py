"""MCP SDK authorization-server provider. GitHub completes the login."""

from __future__ import annotations

import logging
import secrets
import time
import uuid

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fernruf.service.oauth_store import OAuthStore
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.oauth")
SCOPE = "calls"
TXN_TTL_S = 600
CODE_TTL_S = 60


class FernrufOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, settings: Settings, store: OAuthStore, github) -> None:
        self.settings = settings
        self.store = store
        self.github = github

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.save_client(client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not self.github.configured:
            raise AuthorizeError("server_error", "GitHub login is not configured")
        expected = self.settings.oauth_resource.rstrip("/")
        if params.resource and params.resource.rstrip("/") != expected:
            raise AuthorizeError("invalid_target", "resource is not this server")
        txn_id = secrets.token_urlsafe(32)
        scopes = list(params.scopes or [SCOPE])
        self.store.save_txn(
            txn_id,
            {
                "phase": "pending_github",
                "client_id": client.client_id,
                "client_name": client.client_name or client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "scopes": scopes,
                "resource": params.resource or self.settings.oauth_resource,
                "client_state": params.state,
            },
            time.time() + TXN_TTL_S,
        )
        return self.github.authorize_url(txn_id)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.store.load_code(client.client_id, authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not self.store.consume_code(authorization_code.code):
            raise TokenError("invalid_grant", "authorization code does not exist")
        return self._issue(
            client_id=client.client_id,
            client_name=client.client_name or client.client_id,
            github_user_id=authorization_code.subject or "",
            scopes=list(authorization_code.scopes),
            resource=authorization_code.resource,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return self.store.load_refresh(client.client_id, refresh_token)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = time.time()
        ok = self.store.rotate_refresh(
            old_refresh=refresh_token.token,
            new_refresh=refresh,
            new_access=access,
            client_id=client.client_id,
            scopes=list(scopes),
            access_expires_at=now + self.settings.oauth_access_ttl_s,
            refresh_expires_at=now + self.settings.oauth_refresh_ttl_s,
            resource=refresh_token.resource,
            subject=refresh_token.subject,
        )
        if not ok:
            raise TokenError("invalid_grant", "refresh token does not exist")
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=int(self.settings.oauth_access_ttl_s),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.store.access_grant(token)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.store.revoke_all()

    def _issue(
        self,
        *,
        client_id: str,
        client_name: str,
        github_user_id: str,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = time.time()
        self.store.save_grant(
            grant_id=str(uuid.uuid4()),
            client_id=client_id,
            client_name=client_name,
            github_user_id=github_user_id,
            refresh_expires_at=now + self.settings.oauth_refresh_ttl_s,
            access_token=access,
            refresh_token=refresh,
            access_expires_at=now + self.settings.oauth_access_ttl_s,
            scopes=scopes,
            resource=resource,
            subject=github_user_id,
        )
        LOG.info("fernruf oauth grant client_id=%s github_user_id=%s", client_id, github_user_id)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=int(self.settings.oauth_access_ttl_s),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )
