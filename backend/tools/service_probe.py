"""Live health and binding probe for the managed Session Player backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from service_preflight import (
    DEFAULT_CONFIG,
    EXPECTED_HOST,
    EXPECTED_PORT,
    ServicePreflightError,
    _load_config,
    run_preflight,
)


def _get_json(url: str, *, timeout: float) -> dict[str, object]:
    try:
        with urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                raise ServicePreflightError(
                    f"{url} returned HTTP {response.status}"
                )
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServicePreflightError(f"Could not probe {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ServicePreflightError(f"{url} did not return a JSON object")
    return payload


def probe(
    *,
    config_path: Path = DEFAULT_CONFIG,
    timeout: float = 2.0,
) -> dict[str, object]:
    configured_session_id = run_preflight(
        config_path=config_path,
        check_port=False,
    )
    base = f"http://{EXPECTED_HOST}:{EXPECTED_PORT}"
    health = _get_json(f"{base}/health", timeout=timeout)
    if (
        health.get("ok") is not True
        or health.get("service") != "super-band-session-player"
        or health.get("session_persistence") != "ready"
    ):
        raise ServicePreflightError(
            "Health endpoint did not confirm Session Player persistence readiness"
        )
    session = _get_json(
        f"{base}/api/sessions/{configured_session_id}",
        timeout=timeout,
    )
    if session.get("id") != configured_session_id:
        raise ServicePreflightError("Session endpoint returned the wrong binding")
    part = _get_json(
        f"{base}/api/plugin/bass-part?"
        + urlencode({"session_id": configured_session_id}),
        timeout=timeout,
    )
    if part.get("session_id") != configured_session_id:
        raise ServicePreflightError("Bass plugin endpoint returned the wrong binding")
    notes = part.get("notes")
    if not isinstance(notes, list) or not notes:
        raise ServicePreflightError("Configured session has no readable Bass part")
    return {
        "ok": True,
        "service": health["service"],
        "session_id": configured_session_id,
        "tempo": session.get("tempo"),
        "acceptance_fixture_name": session.get("acceptance_fixture_name"),
        "acceptance_receipt_sha256": session.get("acceptance_receipt_sha256"),
        "output_transpose_semitones": part.get("output_transpose_semitones"),
        "bass_note_count": len(notes),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    try:
        result = probe(config_path=args.config, timeout=args.timeout)
    except ServicePreflightError as exc:
        print(f"Session Player service probe failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
