from __future__ import annotations

from n4x.http.inspector import resolve_worker_public_origin
from n4x.system.origins import instance_link, is_loopback_origin


def test_instance_link_keeps_loopback_as_host_bind() -> None:
    link = instance_link("http://127.0.0.1:7744", "/experience/office")
    assert link["origin_kind"] == "host_bind"
    assert link["url"] == "http://127.0.0.1:7744/experience/office"
    assert link["host_bind_url"] == link["url"]


def test_instance_link_prefers_public_origin() -> None:
    link = instance_link("https://box.example", "/development/d1/experience/office")
    assert is_loopback_origin("https://box.example") is False
    assert link["origin_kind"] == "public"
    assert link["url"] == "https://box.example/development/d1/experience/office"
    assert (
        link["host_bind_url"]
        == "http://127.0.0.1:7744/development/d1/experience/office"
    )


def test_resolve_worker_public_origin_passes_through_non_loopback() -> None:
    assert (
        resolve_worker_public_origin("0.0.0.0", 7744, "https://box.example/")
        == "https://box.example"
    )
    assert resolve_worker_public_origin("0.0.0.0", 7744, None) == (
        "http://127.0.0.1:7744"
    )
    assert resolve_worker_public_origin(
        "0.0.0.0", 7744, "http://127.0.0.1:7744"
    ) == "http://127.0.0.1:7744"
