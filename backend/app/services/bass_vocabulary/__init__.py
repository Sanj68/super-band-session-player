"""Sub One Bass Vocabulary module."""

from app.services.bass_vocabulary.pitch_roles import (
    BassVocabularyNoteEvent,
    SUPPORTED_PITCH_ROLES,
    resolve_pitch_role,
    template_to_note_events,
)
from app.services.bass_vocabulary.candidates import (
    VocabularyCandidate,
    generate_template_candidate_events,
    generate_vocabulary_candidates,
    select_templates_for_context,
    should_generate_vocabulary_candidates,
)
from app.services.bass_vocabulary.profile import (
    SUB_ONE_BASS_VOCABULARY_PROFILE_V1,
    SUB_ONE_BASS_REFERENCES,
    SUB_ONE_AVOID_RULES,
    BassVocabularyProfile,
    BassVocabularyReference,
    valid_lanes,
)
from app.services.bass_vocabulary.templates import (
    SUB_ONE_BASS_TEMPLATES,
    BassVocabularyTemplate,
    templates_by_id,
)

__all__ = [
    "BassVocabularyNoteEvent",
    "BassVocabularyProfile",
    "BassVocabularyReference",
    "BassVocabularyTemplate",
    "SUPPORTED_PITCH_ROLES",
    "SUB_ONE_AVOID_RULES",
    "SUB_ONE_BASS_REFERENCES",
    "SUB_ONE_BASS_TEMPLATES",
    "SUB_ONE_BASS_VOCABULARY_PROFILE_V1",
    "VocabularyCandidate",
    "generate_template_candidate_events",
    "generate_vocabulary_candidates",
    "resolve_pitch_role",
    "select_templates_for_context",
    "should_generate_vocabulary_candidates",
    "template_to_note_events",
    "templates_by_id",
    "valid_lanes",
]
