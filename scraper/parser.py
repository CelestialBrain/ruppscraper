"""Title parser for r/RateUPProfs post titles.

Expected format:
    [CAMPUS] Course Code - LASTNAME, FIRSTNAME
    e.g. [UPD] Speech 30 - REDELICIA, ROMEO JOSHUA
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scraper.config import canonical_campus
from scraper.name_resolver import clean_scraped_name, is_plausible_professor_name

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

    # Normalize en/em dashes so "Physics 71 – Pagayon, Julius" matches.
    normalized = (
        title.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )

    # Handle multi-prof titles separated by semicolon ';' or '&'
    # Take the first professor for primary parsing
    primary_title = normalized.split(";")[0].split("&")[0].strip()

    match = _TITLE_RE.match(primary_title)
    if match:
        campus = canonical_campus(match.group("campus"))
        if campus:
            course, last_name, first_name = _clean_name_fields(
                match.group("course"),
                match.group("last_name"),
                match.group("first_name"),
            )
            built = _maybe_build(campus, course, last_name, first_name, title)
            if built is not None:
                return built

    # Try swapped order: [CAMPUS] LastName, FirstName - Course
    match = _TITLE_SWAPPED_RE.match(primary_title)
    if match:
        campus = canonical_campus(match.group("campus"))
        if campus:
            course, last_name, first_name = _clean_name_fields(
                match.group("course"),
                match.group("last_name"),
                match.group("first_name"),
            )
            built = _maybe_build(campus, course, last_name, first_name, title)
            if built is not None:
                return built

    # Try unbracketed order: Course - LastName, FirstName (defaults to UPD)
    match = _TITLE_NO_BRACKET_RE.match(primary_title)
    if match:
        campus = "UPD"
        course, last_name, first_name = _clean_name_fields(
            match.group("course"),
            match.group("last_name"),
            match.group("first_name"),
        )
        built = _maybe_build(campus, course, last_name, first_name, title)
        if built is not None:
            return built

    # Try informal: [CAMPUS] Course - FirstName LastName (no comma)
    match = _TITLE_NO_COMMA_RE.match(primary_title)
    if match:
        full_name = _strip_honorifics(_clean_whitespace(match.group("full_name")))
        name_parts = [p for p in full_name.split() if not _is_section_token(p)]
        if len(name_parts) >= 2:
            campus = canonical_campus(match.group("campus"))
            if campus:
                course = _strip_section_tokens(_clean_whitespace(match.group("course")))
                # Last token = surname; preceding tokens = given names
                last_name = _title_case_name(name_parts[-1])
                first_name = _title_case_name(" ".join(name_parts[:-1]))
                built = _maybe_build(campus, course, last_name, first_name, title)
                if built is not None:
                    return built

    # Try missing dash: [CAMPUS] Course LASTNAME, FIRSTNAME
    no_dash = _parse_no_dash(primary_title)
    if no_dash is not None:
        campus, course, last_name, first_name = no_dash
        course, last_name, first_name = _clean_name_fields(course, last_name, first_name)
        built = _maybe_build(campus, course, last_name, first_name, title)
        if built is not None:
            return built

    return None


def _maybe_build(
    campus: str,
    course: str,
    last_name: str,
    first_name: str,
    raw_title: str,
) -> ParsedTitle | None:
    """Validate cleaned fields and return a ParsedTitle, or None."""
    if not (course and last_name and first_name):
        return None
    if not _looks_like_professor_parse(course, last_name, first_name, raw_title):
        return None
    if not is_plausible_professor_name(last_name, first_name):
        return None
    return ParsedTitle(
        campus=campus,
        course=course,
        last_name=last_name,
        first_name=first_name,
        raw_title=raw_title,
    )


# Section / schedule tokens that leak into course or name fields.
_SECTION_TOKEN_RE = re.compile(
    r"""^
    (?:
        WF[A-Z]{0,2}          # WFX, WFR, WFD, WFV, WF…
      | TH[A-Z]{0,3}          # THY, TH, THFW…
      | TWH(?:FW)?            # TWH / TWHFW
      | MWF
      | TTH
      | MW
      | [A-Z]\d{1,2}          # X7, A1
    )
    $""",
    re.VERBOSE | re.IGNORECASE,
)

_HONORIFIC_RE = re.compile(
    r"^(?:sir|ma'?am|mam|ms\.?|mr\.?|mrs\.?|mx\.?|prof\.?|professor|teacher|doc\.?|dr\.?)$",
    re.IGNORECASE,
)

_CLASSMATE_RE = re.compile(
    r"\b(?:looking for|finding|lf)\b.*\bclassmates?\b|\bclassmates?\b.*\b(?:looking|finding|lf)\b",
    re.IGNORECASE,
)

# Conversational titles that invert into fake names via the no-comma path.
_META_HUNT_RE = re.compile(
    r"\b(?:looking for|thoughts on|help an iska|prerogable|"
    r"allow prerogs?|profs? that allow)\b",
    re.IGNORECASE,
)


def _is_section_token(token: str) -> bool:
    return bool(_SECTION_TOKEN_RE.match(token.strip()))


def _strip_section_tokens(text: str) -> str:
    """Remove schedule/section codes from a course or name fragment."""
    kept = [tok for tok in text.split() if not _is_section_token(tok)]
    # Also drop trailing "WFX:" style prefixes glued with a colon.
    cleaned: list[str] = []
    for tok in kept:
        if ":" in tok:
            left, right = tok.split(":", 1)
            if _is_section_token(left) and right:
                cleaned.append(right)
                continue
            if _is_section_token(left) and not right:
                continue
        cleaned.append(tok)
    return _clean_whitespace(" ".join(cleaned))


_UNDER_PREFIX_RE = re.compile(r"^(?:under|with|by)\s+", re.IGNORECASE)


def _strip_honorifics(text: str) -> str:
    text = _UNDER_PREFIX_RE.sub("", text.strip())
    kept = [tok for tok in text.split() if not _HONORIFIC_RE.match(tok.strip(".,"))]
    return _clean_whitespace(" ".join(kept))


def _split_embedded_course_dashes(
    course: str, last_name: str
) -> tuple[str, str]:
    """Handle 'PE 2 - PHILIPPINE GAMES - BERNALES' → course+=games, last=Bernales."""
    if " - " not in last_name:
        return course, last_name
    parts = [p.strip() for p in last_name.split(" - ") if p.strip()]
    if len(parts) < 2:
        return course, last_name
    # Final segment is the surname; earlier segments belong to the course label.
    return _clean_whitespace(f"{course} - {' - '.join(parts[:-1])}"), parts[-1]


def _clean_name_fields(
    course_raw: str, last_raw: str, first_raw: str
) -> tuple[str, str, str]:
    """Normalize course + name fields after a regex match."""
    course = _strip_section_tokens(_clean_whitespace(course_raw))
    last_name = _strip_honorifics(_strip_section_tokens(_clean_whitespace(last_raw)))
    first_name = _strip_honorifics(_strip_section_tokens(_clean_whitespace(first_raw)))
    course, last_name = _split_embedded_course_dashes(course, last_name)
    # Leading punctuation artifacts from en-dash normalization.
    last_name = last_name.lstrip("-–— ").strip()
    first_name = first_name.lstrip("-–— ").strip()
    last_name, first_name = clean_scraped_name(last_name, first_name)
    return (
        course,
        _title_case_name(last_name) if last_name else "",
        _title_case_name(first_name) if first_name else "",
    )


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

    campus = canonical_campus(match.group("campus"))
    if not campus:
        return None
    return (
        campus,
        _clean_whitespace(course_raw),
        _title_case_name(last_name_raw),
        _title_case_name(first_name_raw),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _looks_like_professor_parse(
    course: str,
    last_name: str,
    first_name: str,
    raw_title: str,
) -> bool:
    """Reject conversational / meta titles that regex-match by accident."""
    if re.fullmatch(r"\d+", last_name.strip()):
        return False
    if "?" in raw_title:
        return False
    if _CLASSMATE_RE.search(raw_title) or _META_HUNT_RE.search(raw_title):
        return False
    # Digits in a "name" almost always mean a botched parse (course code leak).
    if re.search(r"\d", last_name) or re.search(r"\d", first_name):
        return False
    junk_names = {
        "classmates",
        "classmate",
        "looking",
        "finding",
        "math",
        "arts",
        "units",
        "professor",
        "tba",
        "gymnastics",
        "rhythmic",
        "done",
        "pls",
        "pahingi",
        "venue",
        "classroom",
        "email",
        "e-mail",
        "profs",
        "prof",
        "prerogs",
        "thoughts",
        "help",
        "her",
        "suggestions",
        "thesis",
        "validation",
    }
    if last_name.lower().rstrip("!,.") in junk_names:
        return False
    if first_name.lower().rstrip("!,.") in junk_names:
        return False
    # Courses that are whole Filipino sentences / prompts are not course codes.
    lowered = course.lower()
    if any(
        phrase in lowered
        for phrase in (
            "sa mga",
            "nakakuha",
            "anyone",
            "thoughts",
            "paano",
            "reco",
            "recommend",
            "looking for",
            "classmates",
        )
    ):
        return False
    if len(course.split()) > 6 and not re.search(r"\d", course):
        return False
    if len(first_name.split()) > 6:
        return False
    return True


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
