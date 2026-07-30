from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROCESSOR_CPP = ROOT / "audio-midifx" / "Source" / "PluginProcessor.cpp"
POLICY_H = ROOT / "audio-midifx" / "Source" / "ActionStatusPolicy.h"
POLICY_TEST = (
    ROOT / "audio-midifx" / "Tests" / "ActionStatusPolicyTests.cpp"
)


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        token = source[index]
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"Unterminated function body for {signature}")


def test_rejected_producer_statuses_have_native_compile_time_contracts() -> None:
    source = PROCESSOR_CPP.read_text(encoding="utf-8")
    policy_source = POLICY_H.read_text(encoding="utf-8")
    executable_test = POLICY_TEST.read_text(encoding="utf-8")
    policy = _function_body(
        policy_source,
        "constexpr bool shouldPreserveProducerActionStatus (",
    )

    assert "! responseStreamOpened" in policy
    assert "! isSuccessfulHttpStatus (statusCode)" in policy
    for status in (409, 422, 503):
        assert (
            f"static_assert (shouldPreserveProducerActionStatus ({status}, true));"
            in source
        )
    assert (
        "static_assert (shouldPreserveProducerActionStatus (0, false));"
        in source
    )
    assert (
        "static_assert (! shouldPreserveProducerActionStatus (200, true));"
        in source
    )
    assert "std::array<StatusCase, 7>" in executable_test
    assert "shouldPreserveProducerActionStatus (" in executable_test


def test_action_poll_refreshes_part_without_overwriting_rejection() -> None:
    source = PROCESSOR_CPP.read_text(encoding="utf-8")
    polling_loop = _function_body(
        source,
        "void SessionPlayerMidiFXProcessor::run()",
    )

    assert "bool preserveActionStatus = false;" in polling_loop
    assert polling_loop.count(
        "preserveActionStatus = shouldPreserveProducerActionStatus ("
    ) >= 4
    assert "fetchPart (! preserveActionStatus);" in polling_loop
    assert "fetchPart();" not in polling_loop
    assert (
        "preserveActionStatus = false;\n"
        "            setStatus (\"\\\"\" + command + \"\\\" ...\");"
        in polling_loop
    )
