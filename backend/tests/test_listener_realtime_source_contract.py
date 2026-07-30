from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROCESSOR_CPP = ROOT / "audio-listener" / "Source" / "PluginProcessor.cpp"
PROCESSOR_H = ROOT / "audio-listener" / "Source" / "PluginProcessor.h"


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


def test_listener_audio_callback_contains_only_realtime_handoff() -> None:
    source = PROCESSOR_CPP.read_text(encoding="utf-8")
    callback = _function_body(
        source,
        "void SessionPlayerListenerAudioProcessor::processBlock(",
    )
    boundary_handoff = _function_body(
        source,
        "void SessionPlayerListenerAudioProcessor::maybeEmitBarFrame(",
    )

    assert "maybeEmitBarFrame(" in callback
    assert "queueCurrentWindow(" in boundary_handoff
    for forbidden in (
        "analyseWindow(",
        "extractChroma(",
        "performFrequencyOnlyForwardTransform",
        "juce::String",
        "juce::ScopedLock",
        "bridgeClient.pushFrame(",
        "postJson(",
    ):
        assert forbidden not in callback
        assert forbidden not in boundary_handoff


def test_listener_queue_payload_is_preallocated_pod() -> None:
    header = PROCESSOR_H.read_text(encoding="utf-8")
    source = PROCESSOR_CPP.read_text(encoding="utf-8")
    handoff = _function_body(
        source,
        "bool SessionPlayerListenerAudioProcessor::queueCurrentWindow(",
    )

    assert "std::is_trivially_copyable<AnalysisJob>::value" in header
    assert "std::array<AnalysisJob, analysisQueueCapacity>" in header
    assert "analysisFifo.write(1)" in handoff
    assert "std::copy_n(" in handoff
    assert "juce::String" not in handoff
    assert "juce::ScopedLock" not in handoff


def test_listener_worker_owns_analysis_publication_and_ui_lock() -> None:
    source = PROCESSOR_CPP.read_text(encoding="utf-8")
    worker = _function_body(
        source,
        "void SessionPlayerListenerAudioProcessor::processAnalysisJob(",
    )

    assert "analyseWindow(job)" in worker
    assert "bridgeClient.pushFrame(frame)" in worker
    assert "juce::ScopedLock lock(keyLock)" in worker
