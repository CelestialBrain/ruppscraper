# Changelog

## 2026-08-12 — mention resolve gate toward ProfstoPick 80%

### Measured (this machine)

| Check | Result |
| --- | --- |
| Progressive scrape (`scrape-all --scale 0.15`) | +27 posts / +77 comments via **Arctic Shift** (public Reddit `.json` still 403 without OAuth) |
| DB after reparse | **677 posts · 832 comments · 377 parsed (55.7%) · 374 professor rows** |
| Mention resolve sample 100 **recent** UPD | **83%** resolved (8 ambiguous · 9 unresolved) — **PASS ≥80%** |
| Mention resolve sample 100 **random** seed=42 | **87%** resolved — **PASS ≥80%** |
| Unique-professor CRS match (`match`) | **71%** (264/374) — lower than mention sample because orphan/bad-parse professor rows remain in SQLite |

Unresolved mentions are written to `output/resolve_report.json` (and the comments export sidecar); they are not silently attached.

### Code

- Hardened title parser: en/em dashes, section-code strip (`WFX`/`THY`/…), honorific/`under` strip, classmate-hunt rejection, embedded course-dash before surname.
- CRS matcher: accent-fold, reversed/rotated name attempts, hyphen-head and multi-word surname head.
- New `python -m scraper resolve-report` (sample + rate + unresolved list; exit 3 if below 80%).
- New `export --format comments` — ReviewRow-*shaped* rows + `.unresolved.json` sidecar (see README: **not** drop-in for `profstopick` `import/rupp.ts`).

### Still blocking a clean ProfstoPick “importer already reads it” story

1. **No Reddit importer in profstopick yet** — only `script/import/rupp.ts` for the alec numeric dump (`MINIMUM_REVIEW = 7000`, expects 0–5 ratings). Reddit export needs a new source (or a relaxed adapter); see README § ProfstoPick ingest.
2. **Title parse rate ~56%** — conversational / multi-prof / prerog posts stay unparsed (correctly excluded from the mention sample).
3. **Roster gaps** — several well-parsed names (Alejon, Lizarraga, Parena, …) are absent from the current CRS sqlite snapshot; resolve cannot invent them.
4. **OAuth still optional** — without Reddit credentials, listings/comments stay on Arctic Shift (+ RSS).
