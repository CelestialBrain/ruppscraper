"""Professor name resolver — ported from blead's ProfNameResolver.

Handles PH naming patterns that show up on Reddit and Facebook:
  "GARCIA, MARK LESTER B." / "Mark Garcia" / "Sir Garcia" / "Juan Dela Cruz"

Source of truth for the TS original:
  blead/src/fb-group/matchers/name-resolver.ts
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Surname particles that belong to the LAST name in plain "First Last" spelling.
SURNAME_PARTICLES: set[str] = {
    "de",
    "dela",
    "del",
    "delos",
    "delas",
    "dels",
    "di",
    "da",
    "do",
    "san",
    "santa",
    "santo",
    "los",
    "las",
    "von",
    "van",
    "der",
    "den",
    "st",
    "mac",
    "mc",
}

_HONORIFIC_RE = re.compile(
    r"^(?:sir|ma'?am|mam|ms\.?|mr\.?|mrs\.?|mx\.?|prof\.?|professor|"
    r"teacher|doc\.?|dr\.?|fr\.?)$",
    re.IGNORECASE,
)

# Leading section / schedule junk Reddit titles often glue onto the surname.
# Letter runs must include digits (`A1`, `X7`) so particles like De/Di/Del stay.
_LEADING_SECTION_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?|[A-Z]{1,3}\d+|WFX?|WF[A-Z]|TH[XY]|MWF|TTH|"
    r"UNDER|FOR)\s*[-–—:]?\s+",
    re.IGNORECASE,
)

# Trailing first-name tokens that leak in from Reddit titles.
_TRAILING_FIRST_NOISE_RE = re.compile(
    r"(?:\s+(?:e-?mail|pls+|please|prerog(?:ative)?|notes?|help|review|"
    r"units?|section|online|hybrid|sync|async|f2f|lec|lab|classmate?s?|"
    r"gmail|looking(?:\s+for)?))+\s*$",
    re.IGNORECASE,
)

_JUNK_LAST_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?|tba|tbd|n/?a|none|unknown|prerogative|units|"
    r"classmates!?|gymnastics|support|philippine|demo|kas|philo|fil|"
    r"eng|math|chem|bio|econ|speech|review|upx|psych|electives|"
    r"preenlistment|hinay|diliman|pls|done|e-?mail|venue|classroom|"
    r"help|her|profs?|prerogs?|thoughts|allow|looking|finding|gmail|"
    r"classmate?s?|suggestions?|thesis|validation|respondents?|"
    r"canceled|cancelled)$",
    re.IGNORECASE,
)

# Particles that are never a standalone last name ("De, Juan" leftover).
_PARTICLE_ONLY_LAST = {
    "de",
    "di",
    "del",
    "dela",
    "delos",
    "delas",
    "dels",
    "da",
    "do",
    "los",
    "las",
    "der",
    "den",
}


def normalize_name(text: str) -> str:
    """Lowercase, accent-fold, strip punctuation — blead `normalize()`."""
    folded = unicodedata.normalize("NFD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"[.,;:'\"!?()/\\]", "", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    return folded


def fold_key(text: str) -> str:
    """Uppercase accent-folded key for CRS indexes."""
    return normalize_name(text).upper()


def split_name_parts(raw: str) -> tuple[str, str]:
    """Split into (last_raw, first_raw). Comma path preferred; plain First Last
    pulls surname particles into the last name (Juan Dela Cruz → Dela Cruz).
    """
    raw = raw.strip()
    if not raw:
        return "", ""

    # Multi-prof "GO, CLARK K., NABLE, JOB A." → first professor only
    multi = re.match(
        r"^([A-Z][A-Z\s'-]+),\s*([A-Za-z][A-Za-z.\s'-]+?)(?:,\s*[A-Z]{2,},|$)",
        raw,
    )
    term = f"{multi.group(1)}, {multi.group(2).strip()}" if multi else raw
    term = term.strip()

    if "," in term:
        parts = [p.strip() for p in term.split(",")]
        return parts[0] or "", parts[1] if len(parts) > 1 else ""

    tokens = [t for t in term.split() if t]
    if len(tokens) <= 1:
        return term, ""

    li = len(tokens) - 1
    while li - 1 >= 1:
        particle = re.sub(r"[^a-z]", "", tokens[li - 1].lower())
        if particle not in SURNAME_PARTICLES:
            break
        li -= 1
    return " ".join(tokens[li:]), " ".join(tokens[:li])


def strip_honorifics(text: str) -> str:
    kept = [tok for tok in text.split() if not _HONORIFIC_RE.match(tok.strip(".,"))]
    return " ".join(kept).strip()


def clean_scraped_name(last_name: str, first_name: str) -> tuple[str, str]:
    """Normalize Reddit-parsed names before CRS matching.

    Strips honorifics and leading section tokens glued onto surnames
    (e.g. "1 Francisco" → "Francisco", "2 - Castaneda" → "Castaneda").
    """
    last = strip_honorifics(last_name or "")
    first = strip_honorifics(first_name or "")
    last = _LEADING_SECTION_RE.sub("", last).strip(" -–—:")
    first = _TRAILING_FIRST_NOISE_RE.sub("", first).strip()
    # If last still looks like "Francisco" after strip, good; if empty, bail.
    if not last and first:
        # Entire "name" may have been dumped into first_name.
        last, first = split_name_parts(first)
    return last.strip(), first.strip()


def is_plausible_professor_name(last_name: str, first_name: str) -> bool:
    """Reject junk professor rows produced by bad title parses."""
    last, first = clean_scraped_name(last_name, first_name)
    if not last or not first:
        return False
    if _JUNK_LAST_RE.match(last):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", last):
        return False
    if "?" in last or "?" in first:
        return False
    if len(last) < 2:
        return False
    # First name that is itself a course code / section token
    if re.fullmatch(r"[A-Za-z]{1,4}\s*\d+(?:\.\d+)?", first):
        return False
    if len(first.split()) > 8:
        return False
    # Multi-prof dump ("Junio, Roque" / "Plata, Alcasid" in either field)
    if first.count(",") >= 1 or last.count(",") >= 1:
        return False
    if not re.search(r"[A-Za-z]{2,}", last):
        return False
    if normalize_name(last) in _PARTICLE_ONLY_LAST and len(last.split()) == 1:
        return False
    if "@" in last or "@" in first:
        return False
    if re.search(r"\.(?:com|edu)\b", f"{last} {first}", re.IGNORECASE):
        return False
    if re.search(r"[\[\]/&]", last) or re.search(r"[\[\]/&]", first):
        return False
    if re.search(
        r"\b(?:and|&|/|or|vs\.?|reco|prerog|classmates|finding|buddy|"
        r"looking|thoughts|venue|classroom)\b",
        f"{last} {first}",
        re.IGNORECASE,
    ):
        return False
    return True


def generate_variants(search_term: str) -> tuple[list[str], dict[str, str | bool]]:
    """Generate name variants from canonical or plain format."""
    last_raw, first_raw = split_name_parts(search_term)
    last_name = normalize_name(last_raw)
    rest = [p for p in normalize_name(first_raw).split() if p]
    first_name = rest[0] if rest else ""
    first_full = " ".join(p for p in rest if len(p) > 1)
    middle_initial = next((p for p in rest if len(p) == 1), "")
    is_short = len(last_name) <= 3

    variants: list[str] = []
    if first_full:
        variants.append(f"{last_name} {first_full}")
        variants.append(f"{first_full} {last_name}")
    if first_name:
        variants.append(f"{last_name} {first_name}")
        if first_name != first_full:
            variants.append(f"{first_name} {last_name}")
        variants.append(f"{last_name} {first_name[0]}")
        variants.append(f"{first_name[0]} {last_name}")
        if len(rest) >= 2 and len(rest[1]) > 1:
            variants.append(f"{first_name[0]}{rest[1][0]} {last_name}")
    if middle_initial and first_full:
        variants.append(f"{last_name} {first_full} {middle_initial}")

    uniq = []
    seen: set[str] = set()
    for v in variants:
        if len(v) >= 3 and v not in seen:
            seen.add(v)
            uniq.append(v)

    parsed: dict[str, str | bool] = {
        "last_name": last_name,
        "first_name": first_name,
        "first_name_full": first_full,
        "middle_initial": middle_initial,
        "is_short_last_name": is_short,
    }
    return uniq, parsed


@dataclass
class ProfName:
    search_term: str
    last_name: str
    first_name: str
    first_name_full: str
    middle_initial: str
    is_short_last_name: bool
    variant_list: list[str] = field(default_factory=list)


@dataclass
class NameMatch:
    search_term: str
    confidence: float
    matched_variant: str
    method: str


class ProfNameResolver:
    """Blead-style professor name resolver for free-text + roster matching."""

    def __init__(self) -> None:
        self.professor_list: list[ProfName] = []
        self._variant_index: dict[str, ProfName] = {}
        self._last_name_index: dict[str, list[ProfName]] = {}

    def load_professors(self, search_terms: list[str]) -> None:
        self.professor_list = []
        self._variant_index.clear()
        self._last_name_index.clear()

        for term in search_terms:
            variants, parsed = generate_variants(term)
            prof = ProfName(
                search_term=term,
                last_name=str(parsed["last_name"]),
                first_name=str(parsed["first_name"]),
                first_name_full=str(parsed["first_name_full"]),
                middle_initial=str(parsed["middle_initial"]),
                is_short_last_name=bool(parsed["is_short_last_name"]),
                variant_list=variants,
            )
            self.professor_list.append(prof)
            for v in variants:
                self._variant_index.setdefault(v, prof)
            self._last_name_index.setdefault(prof.last_name, []).append(prof)

        # Honorifics only for unique last names (blead post-load step)
        for last, group in self._last_name_index.items():
            if len(group) == 1 and len(last) > 3:
                prof = group[0]
                for h in (f"sir {last}", f"maam {last}", f"prof {last}"):
                    if h not in self._variant_index:
                        self._variant_index[h] = prof
                        prof.variant_list.append(h)

    def mentions_prof(self, text: str, search_term: str) -> float:
        """Return confidence 0–1 that text mentions search_term."""
        norm = normalize_name(text)
        variants, parsed = generate_variants(search_term)
        last = str(parsed["last_name"])
        first = str(parsed["first_name"])
        is_short = bool(parsed["is_short_last_name"])

        for v in variants:
            if len(v) < 5:
                continue
            escaped = re.escape(v)
            if re.search(rf"(?:^|\s){escaped}(?:\s|$)", norm) or (
                len(v) >= 8 and v in norm
            ):
                return 0.95

        if first and len(first) >= 3:
            li, fi = norm.find(last), norm.find(first)
            if li >= 0 and fi >= 0 and abs(li - fi) <= 30:
                return 0.85

        if first:
            pat = rf"{re.escape(last)}[,\s]+{re.escape(first[0])}\.?"
            if re.search(pat, norm):
                return 0.75

        if not is_short and last and last in norm:
            same = self._last_name_index.get(last, [])
            return 0.60 if len(same) == 1 else 0.30

        if is_short and first and re.search(rf"\b{re.escape(last)}\b", norm):
            if first in norm:
                return 0.70

        return 0.0

    def match_text(self, text: str) -> NameMatch | None:
        """Find the best-matching loaded professor in free text."""
        norm = normalize_name(text)
        best: NameMatch | None = None
        for variant, prof in self._variant_index.items():
            if len(variant) < 5:
                continue
            escaped = re.escape(variant)
            if re.search(rf"(?:^|\s){escaped}(?:\s|$)", norm) or (
                len(variant) >= 8 and variant in norm
            ):
                conf = 0.95 if " " in variant else 0.60
                if best is None or conf > best.confidence:
                    best = NameMatch(
                        search_term=prof.search_term,
                        confidence=conf,
                        matched_variant=variant,
                        method="exact",
                    )
                if conf >= 0.95:
                    return best
        return best

    def attribute_comment(
        self, comment_text: str, candidate_profs: list[str]
    ) -> str | None:
        best_prof: str | None = None
        best_conf = 0.0
        for prof in candidate_profs:
            conf = self.mentions_prof(comment_text, prof)
            if conf > best_conf:
                best_conf = conf
                best_prof = prof
        return best_prof if best_conf >= 0.30 else None

    def stats(self) -> dict[str, int]:
        return {
            "professors": len(self.professor_list),
            "variants": len(self._variant_index),
            "unique_last_names": len(self._last_name_index),
        }
