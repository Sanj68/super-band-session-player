"""Fail-closed startup checks for the managed Session Player backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.routes import session_routes  # noqa: E402
from app.services import session_store  # noqa: E402

EXPECTED_HOST = "127.0.0.1"
EXPECTED_PORT = 8001
EXPECTED_BRIDGE_PATH = "/api/bridge"
EXPECTED_PLUGIN_PATH = "/api/plugin"
DEFAULT_CONFIG = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Session Player Bridge"
    / "config.json"
)


class ServicePreflightError(RuntimeError):
    """The backend must not start with the discovered local state."""


def _load_config(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServicePreflightError(
            f"Could not read AU configuration at {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ServicePreflightError("AU configuration must be a JSON object")
    return payload


def _validate_loopback_url(
    raw: object,
    *,
    field_name: str,
    expected_path: str,
) -> None:
    if not isinstance(raw, str):
        raise ServicePreflightError(f"{field_name} must be a URL string")
    parsed = urlparse(raw)
    if (
        parsed.scheme != "http"
        or parsed.hostname != EXPECTED_HOST
        or parsed.port != EXPECTED_PORT
        or parsed.path.rstrip("/") != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ServicePreflightError(
            f"{field_name} must be http://{EXPECTED_HOST}:{EXPECTED_PORT}"
            f"{expected_path}; port 8000 belongs to another local service"
        )


def validate_configured_session(config: dict[str, object]) -> str:
    session_id = config.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ServicePreflightError("AU configuration has no session_id")
    try:
        sessions = session_store.load_sessions(session_routes.StoredSession)
    except session_store.SessionStoreError as exc:
        raise ServicePreflightError(
            f"Session snapshot is not safely restorable: {exc}"
        ) from exc
    if session_id not in sessions:
        raise ServicePreflightError(
            f"Configured session {session_id} does not exist in the durable snapshot"
        )
    return session_id


def assert_port_available(host: str = EXPECTED_HOST, port: int = EXPECTED_PORT) -> None:
    if port == 8000:
        raise ServicePreflightError(
            "Refusing to start Session Player on port 8000; it is reserved locally"
        )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, port))
        except OSError as exc:
            raise ServicePreflightError(
                f"Refusing startup: {host}:{port} is already owned by another process"
            ) from exc


def run_preflight(
    *,
    config_path: Path = DEFAULT_CONFIG,
    check_port: bool = True,
) -> str:
    config = _load_config(config_path)
    _validate_loopback_url(
        config.get("api_base_url"),
        field_name="api_base_url",
        expected_path=EXPECTED_BRIDGE_PATH,
    )
    _validate_loopback_url(
        config.get("plugin_api_base_url"),
        field_name="plugin_api_base_url",
        expected_path=EXPECTED_PLUGIN_PATH,
    )
    session_id = validate_configured_session(config)
    if check_port:
        assert_port_available()
    return session_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--skip-port-check",
        action="store_true",
        help="Validate configuration and session persistence while a service is running.",
    )
    args = parser.parse_args()
    try:
        session_id = run_preflight(
            config_path=args.config,
            check_port=not args.skip_port_check,
        )
    except ServicePreflightError as exc:
        print(f"Session Player service preflight failed: {exc}", file=sys.stderr)
        return 78
    print(
        f"Session Player service preflight passed: "
        f"{EXPECTED_HOST}:{EXPECTED_PORT}, session {session_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
