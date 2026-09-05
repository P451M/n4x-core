from __future__ import annotations

import pytest

from n4x.system.host_control import request_host_control


def test_request_host_control_refuses_public_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("N4X_HOST_CONTROL_ORIGIN", "https://box.example")
    with pytest.raises(RuntimeError, match="must be loopback"):
        request_host_control("GET", "/n4x-host/release")
