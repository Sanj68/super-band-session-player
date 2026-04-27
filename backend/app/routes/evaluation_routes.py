"""Local evaluation harness for bass takes by source clip."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.models.evaluation import (
    BassTakeEvaluation,
    ClipEvaluationRecord,
    ClipEvaluationResponse,
    CreateTakeEvaluationBody,
    EngineEvaluationSummary,
    EvaluationAverages,
    EvaluationSummaryResponse,
    SetReferenceNotesBody,
)
from app.services import evaluation_store as store

router = APIRouter()


def _empty_record(clip_id: str) -> ClipEvaluationRecord:
    return ClipEvaluationRecord(clip_id=clip_id, reference_notes="", takes=[])


def _average_scores(takes: list[BassTakeEvaluation]) -> EvaluationAverages:
    if not takes:
        return EvaluationAverages()
    n = float(len(takes))
    return EvaluationAverages(
        groove_fit=round(sum(t.scores.groove_fit for t in takes) / n, 3),
        harmonic_fit=round(sum(t.scores.harmonic_fit for t in takes) / n, 3),
        phrase_feel=round(sum(t.scores.phrase_feel for t in takes) / n, 3),
        articulation_feel=round(sum(t.scores.articulation_feel for t in takes) / n, 3),
        usefulness=round(sum(t.scores.usefulness for t in takes) / n, 3),
    )


@router.get("/summary", response_model=EvaluationSummaryResponse)
def get_evaluation_summary() -> EvaluationSummaryResponse:
    records = store.load_records()
    all_takes: list[BassTakeEvaluation] = []
    by_engine: dict[str, list[BassTakeEvaluation]] = {}
    for rec in records:
        for take in rec.takes:
            all_takes.append(take)
            eng = (take.bass_engine or "baseline").strip() or "baseline"
            by_engine.setdefault(eng, []).append(take)
    engine_rows = [
        EngineEvaluationSummary(
            engine=engine,
            take_count=len(takes),
            averages=_average_scores(takes),
        )
        for engine, takes in sorted(by_engine.items(), key=lambda kv: kv[0])
    ]
    return EvaluationSummaryResponse(
        total_take_count=len(all_takes),
        overall_averages=_average_scores(all_takes),
        by_engine=engine_rows,
    )


@router.get("/{clip_id}", response_model=ClipEvaluationResponse)
def get_clip_evaluation(clip_id: str) -> ClipEvaluationResponse:
    records = store.load_records()
    idx = store.find_clip_index(records, clip_id)
    if idx is None:
        return ClipEvaluationResponse(record=_empty_record(clip_id.strip()))
    return ClipEvaluationResponse(record=records[idx])


@router.put("/reference-notes", response_model=ClipEvaluationResponse)
def set_reference_notes(body: SetReferenceNotesBody) -> ClipEvaluationResponse:
    records = store.load_records()
    idx = store.find_clip_index(records, body.clip_id)
    if idx is None:
        records.append(ClipEvaluationRecord(clip_id=body.clip_id, reference_notes=body.reference_notes, takes=[]))
        out = records[-1]
    else:
        records[idx].reference_notes = body.reference_notes
        out = records[idx]
    store.save_records(records)
    return ClipEvaluationResponse(record=out)


@router.post("/takes", response_model=ClipEvaluationResponse)
def add_take_evaluation(body: CreateTakeEvaluationBody) -> ClipEvaluationResponse:
    records = store.load_records()
    idx = store.find_clip_index(records, body.clip_id)
    if idx is None:
        records.append(_empty_record(body.clip_id))
        idx = len(records) - 1

    rec = records[idx]
    take = BassTakeEvaluation(
        take_id=body.take_id,
        created_at=datetime.now(timezone.utc),
        session_id=body.session_id,
        bass_engine=body.bass_engine,
        bass_style=body.bass_style,
        bass_player=body.bass_player,
        bass_instrument=body.bass_instrument,
        notes=body.notes,
        scores=body.scores,
    )
    rec.takes = [t for t in rec.takes if t.take_id != body.take_id]
    rec.takes.append(take)
    rec.takes.sort(key=lambda t: t.created_at, reverse=True)
    store.save_records(records)
    return ClipEvaluationResponse(record=rec)

