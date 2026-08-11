"""Rule-based signal & keyword extractor for r/RateUPProfs review text.

Extracts structured ratings/signals from student comments and post bodies without
requiring LLMs or paid APIs. Signal categories:
  - Workload: heavy | moderate | light
  - Grading: generous (unoable) | fair | strict
  - Pedagogy: engaging | clear | reading_heavy | recitation_heavy
  - Attendance: strict | lenient | optional
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Keyword Dictionary
# ---------------------------------------------------------------------------

WORKLOAD_HEAVY_KEYWORDS = {
    "heavy workload", "heavy", "so many requirements", "reqs", "demanding",
    "exhausting", "draining", "time-consuming", "time consuming", "paper-heavy",
    "reading-heavy", "readings", "lots of tasks", "overwhelming",
}

WORKLOAD_LIGHT_KEYWORDS = {
    "light workload", "light", "manageable", "easy reqs", "chill",
    "minimal reqs", "few requirements", "low effort", "relaxed",
}

GRADING_GENEROUS_KEYWORDS = {
    "unoable", "1.0", "flat 1", "generous", "high grades", "easy uno",
    "line of 1", "uno", "gives uno", "fairly high",
}

GRADING_STRICT_KEYWORDS = {
    "strict grader", "hard grader", "low grades", "transmutations",
    "hard to get uno", "stingy", "kuripot", "zero uno", "difficult to pass",
}

GRADING_FAIR_KEYWORDS = {
    "fair grading", "fair", "transparent", "justified", "gives feedback",
}

ATTENDANCE_STRICT_KEYWORDS = {
    "strict attendance", "checks attendance", "mandatory", "deductions for tardiness",
    "strict with attendance", "absence limit", "singko if absent",
}

ATTENDANCE_LENIENT_KEYWORDS = {
    "optional attendance", "lenient attendance", "does not check attendance",
    "no attendance", "no deductions", "recorded sessions",
}

PEDAGOGY_ENGAGING_KEYWORDS = {
    "engaging", "passionate", "fun", "inspiring", "great professor",
    "best prof", "approachable", "kind", "caring", "understanding",
}

PEDAGOGY_CLEAR_KEYWORDS = {
    "clear", "explains well", "master of subject", "knowledgeable",
    "organized", "structured", "good ppt", "clear slides",
}


@dataclass
class ReviewSignals:
    """Aggregated signal metrics extracted from text."""

    workload_mentions: dict[str, int] = field(
        default_factory=lambda: {"heavy": 0, "light": 0, "moderate": 0}
    )
    grading_mentions: dict[str, int] = field(
        default_factory=lambda: {"generous": 0, "fair": 0, "strict": 0}
    )
    attendance_mentions: dict[str, int] = field(
        default_factory=lambda: {"strict": 0, "lenient": 0}
    )
    pedagogy_mentions: dict[str, int] = field(
        default_factory=lambda: {"engaging": 0, "clear": 0}
    )
    keywords_found: set[str] = field(default_factory=set)

    @property
    def dominant_workload(self) -> str | None:
        if self.workload_mentions["heavy"] > self.workload_mentions["light"]:
            return "heavy"
        if self.workload_mentions["light"] > self.workload_mentions["heavy"]:
            return "light"
        if self.workload_mentions["heavy"] > 0 or self.workload_mentions["light"] > 0:
            return "moderate"
        return None

    @property
    def dominant_grading(self) -> str | None:
        g = self.grading_mentions
        max_val = max(g.values())
        if max_val == 0:
            return None
        for k, v in g.items():
            if v == max_val:
                return k
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload": self.dominant_workload,
            "grading": self.dominant_grading,
            "workload_mentions": self.workload_mentions,
            "grading_mentions": self.grading_mentions,
            "attendance_mentions": self.attendance_mentions,
            "pedagogy_mentions": self.pedagogy_mentions,
            "keywords": sorted(self.keywords_found),
        }


def analyze_text(text: str) -> ReviewSignals:
    """Extract review signals and key phrases from text (post body or comment)."""
    text_lower = text.lower()
    signals = ReviewSignals()

    # Workload
    for kw in WORKLOAD_HEAVY_KEYWORDS:
        if kw in text_lower:
            signals.workload_mentions["heavy"] += 1
            signals.keywords_found.add(kw)
    for kw in WORKLOAD_LIGHT_KEYWORDS:
        if kw in text_lower:
            signals.workload_mentions["light"] += 1
            signals.keywords_found.add(kw)

    # Grading
    for kw in GRADING_GENEROUS_KEYWORDS:
        if kw in text_lower:
            signals.grading_mentions["generous"] += 1
            signals.keywords_found.add(kw)
    for kw in GRADING_STRICT_KEYWORDS:
        if kw in text_lower:
            signals.grading_mentions["strict"] += 1
            signals.keywords_found.add(kw)
    for kw in GRADING_FAIR_KEYWORDS:
        if kw in text_lower:
            signals.grading_mentions["fair"] += 1
            signals.keywords_found.add(kw)

    # Attendance
    for kw in ATTENDANCE_STRICT_KEYWORDS:
        if kw in text_lower:
            signals.attendance_mentions["strict"] += 1
            signals.keywords_found.add(kw)
    for kw in ATTENDANCE_LENIENT_KEYWORDS:
        if kw in text_lower:
            signals.attendance_mentions["lenient"] += 1
            signals.keywords_found.add(kw)

    # Pedagogy
    for kw in PEDAGOGY_ENGAGING_KEYWORDS:
        if kw in text_lower:
            signals.pedagogy_mentions["engaging"] += 1
            signals.keywords_found.add(kw)
    for kw in PEDAGOGY_CLEAR_KEYWORDS:
        if kw in text_lower:
            signals.pedagogy_mentions["clear"] += 1
            signals.keywords_found.add(kw)

    return signals
