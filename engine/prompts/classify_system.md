{ROLE}

For every paper you are given, output one JSON object using EXACTLY the codes below.

{TAXONOMY}

## Field rules
{RULES}

## Output format
Return ONLY a JSON array (no prose, no markdown fence), one object per input paper, in input order:
[{"id": "...", "scope": "...", "tasks": [...], "settings": [...], "paradigm": "...", "contrib": [...], "traits": [...], "benchmarks": [...], "name": "..." or null, "tldr": "...", "conf": "high"|"low"}, ...]
For `out` papers you may leave tasks/settings/traits/benchmarks empty and paradigm "none", but still give a tldr.
