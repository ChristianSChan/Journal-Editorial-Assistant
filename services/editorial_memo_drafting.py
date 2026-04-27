"""Editorial memo drafting service."""

from __future__ import annotations

from services.reviewer_comment_parsing import ParsedReviewerComments


def draft_editorial_memo(
    manuscript_title: str,
    abstract: str,
    parsed_comments: ParsedReviewerComments,
    journal_name: str = "",
) -> str:
    """Draft an internal editorial memo."""
    _ = abstract
    _ = parsed_comments
    title = manuscript_title or "Untitled manuscript"
    journal = journal_name or "Journal not specified"
    return f"Editorial memo placeholder for {journal}: {title}"
