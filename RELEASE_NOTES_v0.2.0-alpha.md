# v0.2.0-alpha

Second local-use alpha release of Journal Editorial Assistant.

## Highlights

- Added API settings for OpenAlex, Scopus, Semantic Scholar, Clarivate Reviewer Locator, and Wiley TDM.
- Added richer Semantic Scholar author enrichment, including profile links, known affiliations, aliases, external IDs, publication counts, citation counts, and h-index when available.
- Added color-coded matched-paper labels for topic content, method, and population/context.
- Added institution exclusion suggestions using OpenAlex institution search.
- Added conditional feedback learning so useful/irrelevant feedback is treated as manuscript-specific rather than a global judgment about a reviewer.
- Improved Reviewer Finder ordering around recent matching publications, journal articles, overlapping terms, local feedback, Scopus-backed profiles, and recent activity.
- Added a macOS double-click launcher.
- Kept the public release generic: journal name is free text, and no journal-specific editorial-board lists or private defaults are bundled.

## Important caution

Before enabling LLM assistance with manuscript files, reviewer comments, author
responses, or other confidential editorial material, users should check the
relevant journal, publisher, society, funder, institutional, and platform
policies. Some editorial workflows may restrict or prohibit uploading,
transmitting, or processing unpublished manuscript or peer-review content with
external AI systems.

## Notes

- This remains an alpha release intended for local use.
- Citation metrics, author affiliations, emails, and h-index values depend on third-party metadata coverage and should be verified.
- Generated text and automated flags require editor verification before use.
