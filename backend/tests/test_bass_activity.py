"""Deterministic musical contracts for the bass Activity control."""

from __future__ import annotations

import io

import pretty_midi
import pytest

from app.services import generator


_SEEDS = tuple(range(50))
_STYLES = ("supportive", "melodic", "rhythmic", "slap", "fusion")
_INSTRUMENTS = (
    "finger_bass",
    "fretless_bass",
    "upright_bass",
    "sub_bass",
)


def _render(
    *,
    engine: str,
    style: str = "supportive",
    instrument: str = "finger_bass",
    seed: int,
    density_bias: float | None,
) -> tuple[bytes, str]:
    kwargs: dict[str, object] = {
        "tempo": 96,
        "bar_count": 8,
        "key": "D",
        "scale": "natural_minor",
        "bass_style": style,
        "bass_instrument": instrument,
        "bass_engine": engine,
        "seed": seed,
    }
    if density_bias is not None:
        kwargs["density_bias"] = density_bias
    return generator.generate_bass(**kwargs)


def _note_count(data: bytes) -> int:
    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    return sum(len(instrument.notes) for instrument in midi.instruments)


@pytest.mark.parametrize("engine", ["baseline", "phrase_v2"])
@pytest.mark.parametrize("style", _STYLES)
@pytest.mark.parametrize("instrument", _INSTRUMENTS)
def test_omitted_and_zero_activity_preserve_the_same_legacy_render(
    engine: str,
    style: str,
    instrument: str,
) -> None:
    for seed in _SEEDS[:2]:
        omitted = _render(
            engine=engine,
            style=style,
            instrument=instrument,
            seed=seed,
            density_bias=None,
        )
        explicit_zero = _render(
            engine=engine,
            style=style,
            instrument=instrument,
            seed=seed,
            density_bias=0.0,
        )
        assert explicit_zero == omitted


@pytest.mark.parametrize("engine", ["baseline", "phrase_v2"])
@pytest.mark.parametrize("style", _STYLES)
@pytest.mark.parametrize("instrument", _INSTRUMENTS)
def test_activity_moves_note_density_in_both_directions_over_seed_corpus(
    engine: str,
    style: str,
    instrument: str,
) -> None:
    negative = [
        _note_count(
            _render(
                engine=engine,
                style=style,
                instrument=instrument,
                seed=seed,
                density_bias=-1.0,
            )[0]
        )
        for seed in _SEEDS
    ]
    neutral = [
        _note_count(
            _render(
                engine=engine,
                style=style,
                instrument=instrument,
                seed=seed,
                density_bias=0.0,
            )[0]
        )
        for seed in _SEEDS
    ]
    positive = [
        _note_count(
            _render(
                engine=engine,
                style=style,
                instrument=instrument,
                seed=seed,
                density_bias=1.0,
            )[0]
        )
        for seed in _SEEDS
    ]

    assert sum(negative) < sum(neutral) < sum(positive)
    assert all(
        sparse < natural < active
        for sparse, natural, active in zip(
            negative,
            neutral,
            positive,
            strict=True,
        )
    )


@pytest.mark.parametrize("engine", ["baseline", "phrase_v2"])
@pytest.mark.parametrize("density_bias", [-1.0, 1.0])
def test_nonzero_activity_is_repeatable(
    engine: str,
    density_bias: float,
) -> None:
    first = _render(
        engine=engine,
        seed=240726,
        density_bias=density_bias,
    )
    second = _render(
        engine=engine,
        seed=240726,
        density_bias=density_bias,
    )
    assert second == first
