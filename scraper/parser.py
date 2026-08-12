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

# Quaternary: [CAMPUS] Course - FirstName LastName  (no comma; informal titles)
_TITLE_NO_COMMA_RE = re.compile(
    r"""
    \[
    (?P<campus>[A-Za-z]+)
    \]
    \s*
    (?P<course>.+?)
    \s*-\s*
    (?P<full_name>[^()\[\],]+?)
    \s*
    (?:\(.*\))?
    \s*$
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
        if course and last_name and first_name:
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
        if course and last_name and first_name:
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

    # Try informal: [CAMPUS] Course - FirstName LastName (no comma)
    match = _TITLE_NO_COMMA_RE.match(primary_title)
    if match:
        full_name = _clean_whitespace(match.group("full_name"))
        # Reject if it still looks like "LAST, FIRST" residue or empty
        name_parts = full_name.split()
        if len(name_parts) >= 2:
            campus_raw = match.group("campus").strip()
            campus = CAMPUS_LOOKUP.get(campus_raw.upper(), campus_raw.upper())
            course = _clean_whitespace(match.group("course"))
            # Last token = surname; preceding tokens = given names
            last_name = _title_case_name(name_parts[-1])
            first_name = _title_case_name(" ".join(name_parts[:-1]))
            # Drop honorific-only given names noise later if needed
            if course and last_name and first_name:
                return ParsedTitle(
                    campus=campus,
                    course=course,
                    last_name=last_name,
                    first_name=first_name,
                    raw_title=title,
                )

    # Try missing dash: [CAMPUS] Course LASTNAME, FIRSTNAME
    no_dash = _parse_no_dash(primary_title)
    if no_dash is not None:
        return ParsedTitle(
            campus=no_dash[0],
            course=no_dash[1],
            last_name=no_dash[2],
            first_name=no_dash[3],
            raw_title=title,
        )

    return None


def _parse_no_dash(title: str) -> tuple[str, str, str, str] | None:
    """Parse '[CAMPUS] Course LASTNAME, FIRSTNAME' (no dash separator)."""
    match = re.match(
        r"""
        \[
        (?P<campus>[A-Za-z]+)
        \]
        \s+
        (?P<body>.+)
        $
        """,
        title.strip(),
        re.VERBOSE | re.IGNORECASE,
    )
    if not match:
        return None

    body = _clean_whitespace(match.group("body"))
    # Dash forms are handled by earlier regexes; skip if a dash separator remains.
    if "," not in body or re.search(r"\s-\s", body):
        return None

    before, after = body.rsplit(",", 1)
    before = before.strip()
    first_name_raw = after.strip()
    if not before or not first_name_raw:
        return None

    tokens = before.split()
    if len(tokens) < 2:
        return None

    # Prefer splitting after the last token that contains a digit
    # (e.g. "SOC SCI 2 PAGUIRIGAN", "Fil 40 Dela Cruz", "PA 141 Diñgal").
    split_idx: int | None = None
    for i, tok in enumerate(tokens):
        if re.search(r"\d", tok):
            split_idx = i

    if split_idx is not None and split_idx < len(tokens) - 1:
        course_raw = " ".join(tokens[: split_idx + 1])
        last_name_raw = " ".join(tokens[split_idx + 1 :])
    else:
        # Fallback: last token = surname (weak for compound surnames).
        last_name_raw = tokens[-1]
        course_raw = " ".join(tokens[:-1])
        if not course_raw:
            return None

    campus_raw = match.group("campus").strip()
    campus = CAMPUS_LOOKUP.get(campus_raw.upper(), campus_raw.upper())
    return (
        campus,
        _clean_whitespace(course_raw),
        _title_case_name(last_name_raw),
        _title_case_name(first_name_raw),
    )


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
