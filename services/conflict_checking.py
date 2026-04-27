"""Conflict checking service."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.reviewer_retrieval import ReviewerCandidate


class ConflictCheckInput(BaseModel):
    excluded_author_names: list[str] = Field(default_factory=list)
    excluded_institutions: list[str] = Field(default_factory=list)
    exclude_same_institution: bool = True


def check_conflicts(
    candidates: list[ReviewerCandidate],
    conflict_input: ConflictCheckInput,
) -> list[ReviewerCandidate]:
    """Annotate conflict flags and filter candidates when requested."""
    excluded_names = [
        name.casefold() for name in conflict_input.excluded_author_names if name.strip()
    ]
    excluded_institutions = [
        institution.casefold()
        for institution in conflict_input.excluded_institutions
        if institution.strip()
    ]

    filtered: list[ReviewerCandidate] = []
    for candidate in candidates:
        candidate_name = candidate.name.casefold()
        affiliation = (candidate.affiliation or "").casefold()

        has_name_conflict = any(name in candidate_name for name in excluded_names)
        has_institution_conflict = any(
            institution in affiliation for institution in excluded_institutions
        )
        candidate.conflict_flags = []
        if has_name_conflict:
            candidate.conflict_flags.append("Name matches excluded author list")
        if has_institution_conflict:
            candidate.conflict_flags.append("Affiliation matches excluded institution")

        if has_name_conflict:
            continue
        if has_institution_conflict and conflict_input.exclude_same_institution:
            continue

        filtered.append(candidate)

    return filtered
