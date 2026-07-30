from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROCESSOR_CPP = ROOT / "audio-midifx" / "Source" / "PluginProcessor.cpp"
PROCESSOR_H = ROOT / "audio-midifx" / "Source" / "PluginProcessor.h"


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


def test_bass_part_owns_explicit_output_register() -> None:
    header = PROCESSOR_H.read_text(encoding="utf-8")
    source = PROCESSOR_CPP.read_text(encoding="utf-8")

    assert "int outputTransposeSemitones = 0;" in header
    assert '"output_transpose_semitones"' in source
    assert "fresh->outputTransposeSemitones" in source


def test_output_register_changes_playback_identity_and_audible_pitch() -> None:
    source = PROCESSOR_CPP.read_text(encoding="utf-8")
    fingerprint = _function_body(source, "std::uint64_t playbackFingerprintFor (")
    callback = _function_body(
        source,
        "void SessionPlayerMidiFXProcessor::processBlock (",
    )

    assert "part.outputTransposeSemitones" in fingerprint
    assert "n.pitch + part->outputTransposeSemitones" in callback
    assert "noteOn (1, outputPitch" in callback
    assert "active_.push_back ({ outputPitch" in callback
