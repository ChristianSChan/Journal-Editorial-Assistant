"""Institution name suggestions for conflict exclusions."""

from __future__ import annotations

import requests

from services.openalex_config import openalex_headers, openalex_params

OPENALEX_INSTITUTIONS_URL = "https://api.openalex.org/institutions"
REQUEST_TIMEOUT_SECONDS = 8


def suggest_openalex_institutions(query: str, limit: int = 8) -> list[str]:
    """Return institution display-name suggestions from OpenAlex."""
    query = query.strip()
    if len(query) < 3:
        return []

    try:
        response = requests.get(
            OPENALEX_INSTITUTIONS_URL,
            params=openalex_params({"search": query, "per-page": limit}),
            headers=openalex_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    suggestions: list[str] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        name = item.get("display_name")
        if isinstance(name, str) and name.strip() and name not in suggestions:
            suggestions.append(name.strip())
    return suggestions
