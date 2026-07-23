from app.services.source_analysis import build_source_analysis
from app.services.source_analysis_fusion import fuse_source_and_groove


class _Session:
    tempo = 88
    key = "D"
    scale = "natural_minor"
    bar_count = 4
    chord_progression = None


def test_fusion_keeps_source_harmony_and_takes_groove_timing() -> None:
    musical = build_source_analysis(_Session()).model_copy(
        update={
            "source_lane": "reference_audio",
            "tonal_center_pc_guess": 2,
            "scale_mode_guess": "minor",
            "source_kick_weight": [[0.0] * 16 for _ in range(4)],
        }
    )
    kick_rows = [[1.0 if slot in (0, 6, 8) else 0.0 for slot in range(16)] for _ in range(4)]
    groove = build_source_analysis(_Session()).model_copy(
        update={
            "source_lane": "groove_reference_audio",
            "tonal_center_pc_guess": 9,
            "scale_mode_guess": "major",
            "tempo_estimate_bpm": 88.25,
            "source_kick_weight": kick_rows,
            "source_snare_weight": [[1.0 if slot in (4, 12) else 0.0 for slot in range(16)] for _ in range(4)],
            "source_groove_confidence": [0.9] * 4,
        }
    )

    fused = fuse_source_and_groove(musical, groove)

    assert fused.source_lane == "musical_source+groove_reference"
    assert fused.tonal_center_pc_guess == 2
    assert fused.scale_mode_guess == "minor"
    assert fused.tempo_estimate_bpm == 88.25
    assert fused.source_kick_weight == kick_rows
    assert fused.source_metadata["analysis_fusion"]["groove_source"] == "groove_reference_audio"


def test_fusion_without_groove_returns_musical_source() -> None:
    musical = build_source_analysis(_Session())

    assert fuse_source_and_groove(musical, None) is musical
