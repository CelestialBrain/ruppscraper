"""Title parser for r/RateUPProfs post titles.

Expected format:
    [CAMPUS] Course Code - LASTNAME, FIRSTNAME
    e.g. [UPD] Speech 30 - REDELICIA, ROMEO JOSHUA
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scraper.config import CAMPUS_LOOKUP

# ---------------------------------------------------------------------------
# Dataclass for parsed results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedTitle:
    """Structured fields extracted from a r/RateUPProfs post title."""

    campus: str          # Canonical campus code, e.g. "UPD"
    course: str          # Course name/code, e.g. "Speech 30"
    last_name: str       # Professor last name, title-cased
    first_name: str      # Professor first name, title-cased
    raw_title: str       # Original unparsed title

    @property
    def professor_full(self) -> str:
        """Return 'Last Name, First Name'."""
        return f"{self.last_name}, {self.first_name}"

    @property
    def professor_id(self) -> str:
        """Return a normalized key like 'upd__redelicia__romeo_joshua'."""
        parts = [
            self.campus.lower(),
            _normalize_for_id(self.last_name),
            _normalize_for_id(self.first_name),
        ]
        return "__".join(parts)


# ---------------------------------------------------------------------------
# Regex for the title format
# ---------------------------------------------------------------------------

# Standard regex: [CAMPUS] Course - LASTNAME, FIRSTNAME
_TITLE_RE = re.compile(
    r"""
    \[                          # opening bracket
    (?P<campus>[A-Za-z]+)       # campus code: UPD, UPLB, UPMin, etc.
    \]                          # closing bracket
    \s*                         # optional whitespace
    (?P<course>.+?)             # course name/code (non-greedy)
    \s*-\s*                     # dash separator with optional whitespace
    (?P<last_name>[^,]+?)       # last name (everything before comma)
    \s*,\s*                     # comma separator
    (?P<first_name>[^()\[\]]+?) # first name (stop before parens/brackets)
    \s*                         # trailing whitespace
    (?:\(.*\))?                 # optional parenthetical (AY 2024-2025) — discarded
    \s*$                        # end of string
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Secondary regex: [CAMPUS] LASTNAME, FIRSTNAME - Course
_TITLE_SWAPPED_RE = re.compile(
    r"""
    \[                          # opening bracket
    (?P<campus>[A-Za-z]+)       # campus code
    \]                          # closing bracket
    \s*                         # optional whitespace
    (?P<last_name>[^,]+?)       # last name
    \s*,\s*                     # comma separator
    (?P<first_name>[^()\[\]]+?) # first name
    \s*-\s*                     # dash separator
    (?P<course>.+?)             # course name/code
    \s*                         # trailing whitespace
    (?:\(.*\))?                 # optional parenthetical
    \s*$                        # end of string
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Tertiary regex: Course - LASTNAME, FIRSTNAME (missing campus brackets, default UPD)
# Excludes titles starting with campus names like "UPD Fil 40"
_TITLE_NO_BRACKET_RE = re.compile(
    r"""
    ^\s*
    (?!UPD\s|UPLB\s|UPM\s|UPOU\s|UPV\s|UPMin\s|UPB\s|UPC\s|UPT\s) # negative lookahead for campus codes
    (?P<course>[A-Za-z0-9\s]+?) # course name/code
    \s*-\s*                     # dash separator
    (?P<last_name>[^,]+?)       # last name
    \s*,\s*                     # comma separator
    (?P<first_name>[^()\[\]]+?) # first name
    \s*                         # trailing whitespace
    (?:\(.*\))?                 # optional parenthetical
    \s*$                        # end of string
    """,
    re.VERBOSE | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_title(title: str) -> ParsedTitle | None:
    """Parse a r/RateUPProfs post title into structured fields.

    Handles standard format: [CAMPUS] Course - LastName, FirstName
    Also handles swapped order, multi-professor split, and unbracketed titles.
    """
    title = title.strip()
    if not title:
        return None

    # Handle multi-prof titles separated by semicolon ';' or '&'
    # Take the first professor for primary parsing
    primary_title = title.split(";")[0].split("&")[0].strip()

    match = _TITLE_RE.match(primary_title)
    if match:
        campus_raw = match.group("campus").strip()
        campus = CAMPUS_LOOKUP.get(campus_raw.upper(), campus_raw.upper())
        course = _clean_whitespace(match.group("course"))
        last_name = _title_case_name(match.group("last_name").strip())
        first_name = _title_case_name(match.group("first_name").strip())
        return ParsedTitle(
            campus=campus,
            course=course,
            last_name=last_name,
            first_name=first_name,
            raw_title=title,
        )

    # Try swapped order: [CAMPUS] LastName, FirstName - Course
    match = _TITLE_SWAPPED_RE.match(primary_title)
    if match:
        campus_raw = match.group("campus").strip()
        campus = CAMPUS_LOOKUP.get(campus_raw.upper(), campus_raw.upper())
        course = _clean_whitespace(match.group("course"))
        last_name = _title_case_name(match.group("last_name").strip())
        first_name = _title_case_name(match.group("first_name").strip())
        return ParsedTitle(
            campus=campus,
            course=course,
            last_name=last_name,
            first_name=first_name,
            raw_title=title,
        )

    # Try unbracketed order: Course - LastName, FirstName (defaults to UPD)
    match = _TITLE_NO_BRACKET_RE.match(primary_title)
    if match:
        campus = "UPD"
        course = _clean_whitespace(match.group("course"))
        last_name = _title_case_name(match.group("last_name").strip())
        first_name = _title_case_name(match.group("first_name").strip())
        return ParsedTitle(
            campus=campus,
            course=course,
            last_name=last_name,
            first_name=first_name,
            raw_title=title,
        )

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clean_whitespace(text: str) -> str:
    """Collapse multiple whitespace chars into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _title_case_name(name: str) -> str:
    """Title-case a name while preserving abbreviations like 'Ma.' and suffixes.

    Examples:
        "REDELICIA"       → "Redelicia"
        "ROMEO JOSHUA"    → "Romeo Joshua"
        "DE LA CRUZ"      → "De La Cruz"
        "Ma."             → "Ma."
        "SANTOS-REYES"    → "Santos-Reyes"
        "JR."             → "Jr."
    """
    parts: list[str] = []
    for word in name.split():
        if "-" in word:
            # Hyphenated: title-case each segment
            parts.append("-".join(seg.capitalize() for seg in word.split("-")))
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


def _normalize_for_id(text: str) -> str:
    """Lowercase, strip punctuation, collapse spaces → underscores."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "_", text)
    return text
