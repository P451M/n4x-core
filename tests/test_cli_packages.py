from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from n4x.cli.oauth import LoginError, bearer_for
from n4x.cli.packages import PackageTransferError, fetch_archive, stage_archive
from n4x.cli.session import CliSession, is_loopback_origin, save_session
from n4x.secrets.backends import InMemorySecretBackend
from n4x.system.http import create_system_http_app
from n4x.testing import create_test_runtime


def test_loopback_origin_needs_no_bearer() -> None:
    assert is_loopback_origin("http://127.0.0.1:7744")
    assert is_loopback_origin("http://localhost:7744")
    assert not is_loopback_origin("https://box.example")


def test_cli_stage_and_fetch_on_loopback(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    runtime = create_test_runtime()
    starlette = TestClient(create_system_http_app(runtime))

    class _Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.url.query:
                path = f"{path}?{request.url.query}"
            response = starlette.request(
                request.method,
                path,
                content=request.read(),
                headers=request.headers,
            )
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers=response.headers,
            )

    client = httpx.Client(
        transport=_Transport(),
        base_url="http://127.0.0.1:7744",
    )
    source = tmp_path / "office.n4xp"
    source.write_bytes(b"office-bytes")
    store = InMemorySecretBackend()

    staged = stage_archive(
        "http://127.0.0.1:7744",
        source,
        store=store,
        http=client,
    )
    assert staged["archive_name"] == "office.n4xp"
    assert staged["size"] == 12

    output = tmp_path / "downloaded.n4xp"
    fetch_archive(
        "http://127.0.0.1:7744",
        "office.n4xp",
        output,
        store=store,
        http=client,
    )
    assert output.read_bytes() == b"office-bytes"


def test_cli_stage_sends_bearer_and_hash(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        seen["sha"] = request.headers.get("content-sha256", "")
        body = request.read()
        return httpx.Response(
            200,
            json={
                "archive_name": "office.n4xp",
                "size": len(body),
                "content_sha256": seen["sha"],
            },
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://box.example",
    )
    store = InMemorySecretBackend()
    save_session(
        store,
        CliSession(origin="https://box.example", access_token="operator-token"),
    )
    source = tmp_path / "office.n4xp"
    source.write_bytes(b"abc")
    staged = stage_archive(
        "https://box.example",
        source,
        store=store,
        http=http,
    )
    assert staged["archive_name"] == "office.n4xp"
    assert seen["authorization"] == "Bearer operator-token"
    assert seen["sha"] == "sha256:" + hashlib.sha256(b"abc").hexdigest()


def test_cli_stage_asks_for_login_on_401(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://box.example",
    )
    store = InMemorySecretBackend()
    save_session(
        store,
        CliSession(origin="https://box.example", access_token="stale"),
    )
    source = tmp_path / "office.n4xp"
    source.write_bytes(b"abc")
    with pytest.raises(PackageTransferError, match="n4x login"):
        stage_archive("https://box.example", source, store=store, http=http)


def test_bearer_for_requires_login() -> None:
    with pytest.raises(LoginError, match="not logged in"):
        bearer_for("https://box.example", store=InMemorySecretBackend())


def test_cli_session_roundtrip() -> None:
    from n4x.cli.session import load_session

    store = InMemorySecretBackend()
    save_session(
        store,
        CliSession(origin="https://box.example", access_token="tok"),
    )
    loaded = load_session(store, "https://box.example/")
    assert loaded is not None
    assert loaded.access_token == "tok"
    assert loaded.origin == "https://box.example"
