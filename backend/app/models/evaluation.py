"""Models for local bass take evaluation workflow."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class BassTakeScores(BaseModel):
    groove_fit: int = Field(ge=1, le=5)
    harmonic_fit: int = Field(ge=1, le=5)
    phrase_feel: int = Field(ge=1, le=5)
    articulation_feel: int = Field(ge=1, le=5)
    usefulness: int = Field(ge=1, le=5)


class BassTakeEvaluation(BaseModel):
    take_id: str = Field(min_length=1, max_length=120)
    created_at: datetime
    session_id: str = Field(min_length=1)
    bass_engine: str = Field(default="baseline")
    bass_style: str = Field(default="supportive")
    bass_player: str | None = None
    bass_instrument: str = Field(default="finger_bass")
    notes: str = Field(default="", max_length=4000)
    scores: BassTakeScores


class ClipEvaluationRecord(BaseModel):
    clip_id: str = Field(min_length=1, max_length=160)
    reference_notes: str = Field(default="", max_length=12000)
    takes: list[BassTakeEvaluation] = Field(default_factory=list)

    @field_validator("clip_id")
    @classmethod
    def strip_clip_id(cls, v: str) -> str:
        t = v.strip()
        if not t:
            raise ValueError("clip_id cannot be empty")
        return t


class SetReferenceNotesBody(BaseModel):
    clip_id: str = Field(min_length=1, max_length=160)
    reference_notes: str = Field(default="", max_length=12000)


class CreateTakeEvaluationBody(BaseModel):
    clip_id: str = Field(min_length=1, max_length=160)
    take_id: str = Field(min_length=1, max_length=120)
    session_id: str = Field(min_length=1)
    bass_engine: str = Field(default="baseline")
    bass_style: str = Field(default="supportive")
    bass_player: str | None = None
    bass_instrument: str = Field(default="finger_bass")
    notes: str = Field(default="", max_length=4000)
    scores: BassTakeScores


class ClipEvaluationResponse(BaseModel):
    record: ClipEvaluationRecord


class EvaluationAverages(BaseModel):
    groove_fit: float = 0.0
    harmonic_fit: float = 0.0
    phrase_feel: float = 0.0
    articulation_feel: float = 0.0
    usefulness: float = 0.0


class EngineEvaluationSummary(BaseModel):
    engine: str
    take_count: int = 0
    averages: EvaluationAverages = Field(default_factory=EvaluationAverages)


class EvaluationSummaryResponse(BaseModel):
    total_take_count: int = 0
    overall_averages: EvaluationAverages = Field(default_factory=EvaluationAverages)
    by_engine: list[EngineEvaluationSummary] = Field(default_factory=list)

