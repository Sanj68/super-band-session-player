from __future__ import annotations

import json
from pathlib import Path
import socket
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import service_preflight  # noqa: E402


def _config(path: Path, *, port: int = 8001, session_id: str = "fixture") -> Path:
    path.write_text(
        json.dumps(
            {
                "api_base_url": f"http://127.0.0.1:{port}/api/bridge",
                "plugin_api_base_url": (
                    f"http://127.0.0.1:{port}/api/plugin"
                ),
                "session_id": session_id,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_preflight_accepts_bound_loopback_8001_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "config.json")
    monkeypatch.setattr(
        service_preflight.session_store,
        "load_sessions",
        lambda _session_type: {"fixture": object()},
    )

    assert (
        service_preflight.run_preflight(
            config_path=config,
            check_port=False,
        )
        == "fixture"
    )


def test_preflight_rejects_autofactory_port_8000(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path / "config.json", port=8000)

    with pytest.raises(
        service_preflight.ServicePreflightError,
        match="port 8000 belongs to another local service",
    ):
        service_preflight.run_preflight(
            config_path=config,
            check_port=False,
        )


def test_preflight_rejects_missing_configured_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "config.json")
    monkeypatch.setattr(
        service_preflight.session_store,
        "load_sessions",
        lambda _session_type: {},
    )

    with pytest.raises(
        service_preflight.ServicePreflightError,
        match="does not exist",
    ):
        service_preflight.run_preflight(
            config_path=config,
            check_port=False,
        )


def test_port_collision_fails_closed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        owner.bind(("127.0.0.1", 0))
        port = int(owner.getsockname()[1])

        with pytest.raises(
            service_preflight.ServicePreflightError,
            match="already owned",
        ):
            service_preflight.assert_port_available(port=port)
