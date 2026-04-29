"""Editorial-board hooks for public releases.

The public template does not bundle journal-specific editorial-board lists.
Users can still enter any journal name; reviewer discovery remains based on
scholarly publication evidence from the configured metadata sources.
"""

from __future__ import annotations

from services.reviewer_retrieval import ReviewerCandidate


def editorial_board_lookup_note(journal_name: str) -> str:
    if not journal_name.strip():
        return "Not checked: no journal name entered."
    return (
        "Not checked: this public template does not bundle journal-specific "
        "editorial-board sources."
    )


def mark_editorial_board_members(
    candidates: list[ReviewerCandidate],
    journal_name: str,
) -> list[ReviewerCandidate]:
    status = editorial_board_lookup_note(journal_name)
    for candidate in candidates:
        candidate.is_editorial_board_member = False
        candidate.editorial_board_source = None
        candidate.editorial_board_status = status
    return candidates
