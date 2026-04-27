"""Reviewer comment parsing service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedReviewerComments(BaseModel):
    raw_comments: str
    summary: str
    major_concerns: list[str] = Field(default_factory=list)
    minor_concerns: list[str] = Field(default_factory=list)


def parse_reviewer_comments(raw_comments: str) -> ParsedReviewerComments:
    """Parse reviewer comments into editorial concerns."""
    summary = "Comment parsing placeholder. Structured parsing will be added later."
    if raw_comments.strip():
        summary = "Reviewer comments received. Structured parsing will be added later."

    return ParsedReviewerComments(raw_comments=raw_comments, summary=summary)
