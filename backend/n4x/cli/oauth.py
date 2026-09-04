"""Public PKCE login against the instance authorization server."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from n4x.cli.session import (
    CliSession,
    SecretBackend,
    delete_session,
    load_session,
    normalize_origin,
    save_session,
    session_from_token_payload,
)

OpenBrowser = Callable[[str], bool]
HttpClient = httpx.Client

_CLIENT_NAME = "N4X CLI"
_SCOPES = "openid email profile"


class LoginError(RuntimeError):
    pass


def login(
    origin: str,
    *,
    store: SecretBackend,
    http: HttpClient | None = None,
    open_browser: OpenBrowser = webbrowser.open,
) -> CliSession:
    origin = normalize_origin(origin)
    client = http or httpx.Client(timeout=30.0)
    owns_client = http is None
    listener: _AuthListener | None = None
    try:
        metadata = _protected_resource(client, origin)
        authorization_servers = metadata.get("authorization_servers")
        if not isinstance(authorization_servers, list) or not authorization_servers:
            raise LoginError("protected resource metadata has no authorization_servers")
        as_metadata = _authorization_server(client, str(authorization_servers[0]))
        listener = _AuthListener()
        redirect = listener.redirect_uri
        registration = _register_client(
            client, as_metadata, origin, redirect_uri=redirect
        )
        verifier = secrets.token_urlsafe(64)
        challenge = _s256_challenge(verifier)
        state = secrets.token_urlsafe(24)
        authorize_url = (
            f"{as_metadata['authorization_endpoint']}?{urlencode({
                'response_type': 'code',
                'client_id': registration['client_id'],
                'redirect_uri': redirect,
                'scope': _SCOPES,
                'state': state,
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
            })}"
        )
        listener.expect(state)
        listener.start()
        if not open_browser(authorize_url):
            listener.close()
            raise LoginError(f"open this URL to finish login:\n{authorize_url}")
        code = listener.wait(timeout=300)
        token_endpoint = str(as_metadata["token_endpoint"])
        tokens = _exchange(
            client,
            token_endpoint,
            client_id=registration["client_id"],
            code=code,
            verifier=verifier,
            redirect_uri=redirect,
        )
        session = session_from_token_payload(
            origin,
            tokens,
            token_endpoint=token_endpoint,
            client_id=registration["client_id"],
        )
        save_session(store, session)
        return session
    finally:
        if listener is not None:
            listener.close()
        if owns_client:
            client.close()


def logout(origin: str, *, store: SecretBackend) -> None:
    delete_session(store, normalize_origin(origin))


def bearer_for(
    origin: str,
    *,
    store: SecretBackend,
    http: HttpClient | None = None,
) -> str:
    session = load_session(store, origin)
    if session is None:
        raise LoginError(f"not logged in to {normalize_origin(origin)}; run n4x login")
    if not session.expired():
        return session.access_token
    if not session.refresh_token or not session.token_endpoint or not session.client_id:
        raise LoginError(f"session for {session.origin} expired; run n4x login")
    client = http or httpx.Client(timeout=30.0)
    owns_client = http is None
    try:
        response = client.post(
            session.token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": session.refresh_token,
                "client_id": session.client_id,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise LoginError("refresh failed; run n4x login")
        payload = response.json()
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise LoginError("refresh returned an invalid token response")
        refreshed = session_from_token_payload(
            session.origin,
            payload,
            token_endpoint=session.token_endpoint,
            client_id=session.client_id,
            previous=session,
        )
        save_session(store, refreshed)
        return refreshed.access_token
    finally:
        if owns_client:
            client.close()


def _protected_resource(client: HttpClient, origin: str) -> dict[str, Any]:
    url = urljoin(origin.rstrip("/") + "/", ".well-known/oauth-protected-resource")
    response = client.get(url, headers={"Accept": "application/json"})
    if response.status_code >= 400:
        raise LoginError(
            f"cannot read protected resource metadata from {origin} ({response.status_code})"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise LoginError("protected resource metadata is not an object")
    return payload


def _authorization_server(client: HttpClient, issuer: str) -> dict[str, Any]:
    issuer = issuer.rstrip("/")
    url = issuer + "/.well-known/oauth-authorization-server"
    response = client.get(url, headers={"Accept": "application/json"})
    if response.status_code >= 400:
        raise LoginError(
            f"cannot read authorization server metadata from {issuer} ({response.status_code})"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise LoginError("authorization server metadata is not an object")
    for key in ("authorization_endpoint", "token_endpoint"):
        if key not in payload:
            raise LoginError(f"authorization server metadata missing {key}")
    return payload


def _register_client(
    client: HttpClient,
    as_metadata: dict[str, Any],
    origin: str,
    *,
    redirect_uri: str,
) -> dict[str, str]:
    endpoint = as_metadata.get("registration_endpoint")
    if not endpoint:
        raise LoginError(
            "authorization server has no registration_endpoint; "
            "the CLI is a public PKCE client"
        )
    response = client.post(
        str(endpoint),
        json={
            "client_name": _CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": "native",
            "scope": _SCOPES,
            "client_uri": origin,
        },
        headers={"Accept": "application/json"},
    )
    if response.status_code >= 400:
        raise LoginError(f"dynamic client registration failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict) or "client_id" not in payload:
        raise LoginError("registration returned no client_id")
    return {"client_id": str(payload["client_id"])}


class _AuthListener:
    def __init__(self) -> None:
        self._state = ""
        self._code = ""
        self._error = ""
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                query = parse_qs(urlsplit(self.path).query)
                if query.get("code") and query.get("state", [""])[0] == listener._state:
                    listener._code = query["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"N4X CLI signed in. You can close this window.")
                    return
                listener._error = query.get("error", ["authorization failed"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"N4X CLI login failed.")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_port}/callback"
        self._thread = threading.Thread(target=self._server.handle_request, daemon=True)
        self._closed = False

    def expect(self, state: str) -> None:
        self._state = state

    def start(self) -> None:
        self._thread.start()

    def wait(self, *, timeout: float) -> str:
        self._thread.join(timeout=timeout)
        self.close()
        if self._code:
            return self._code
        raise LoginError(self._error or "authorization did not complete")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.server_close()


def _exchange(
    client: HttpClient,
    token_endpoint: str,
    *,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    response = client.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
    )
    if response.status_code >= 400:
        raise LoginError(f"token exchange failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise LoginError("token response is missing access_token")
    return payload


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
