# Changelog

## 2026-08-13 — exhaust triage of every post body and comment

### Measured (this machine)

| Check | Result |
| --- | --- |
| Comments classified | **57,935 / 57,935** — **22,264** review · **35,671** dropped |
| Post bodies classified | **14,098 / 14,098** — **3,308** review · **10,790** dropped |
| `export --format comments --crs` | **10,147** review rows (was 27,586 before triage) |
| Second `triage-reviews` | identical counts (dry) |

Rows are flagged (`is_review`), not deleted. Drop reasons: request / too_short / thanks / ask / reaction / empty / thin.

### Code

- `python -m scraper triage-reviews` walks every `posts.selftext` and `comments.body`.
- `export --format comments` emits only `is_review = 1` (no title-as-fake-review fallback).
- Matrix and sequential scrape jobs run triage before export.

## 2026-08-13 — roster cleanup, Arctic comment paging, full-corpus resolve

### Measured (this machine, ~14k archive)

| Check | Result |
| --- | --- |
| DB after `reparse --all` + `clean-junk` + comment enrich | **14,098 posts · 57,935 comments · 5,392 parsed (38.2%) · 1,872 professor rows** |
| Junk purge | **50 + 7** implausible rows removed; **237** duplicate groups collapsed (**268** extra rows) |
| Mention resolve sample 100 **random** seed=42 | **95%** resolved (2 ambiguous · 3 unresolved) — **PASS ≥80%** |
| Mention resolve sample 100 **recent** UPD | **87%** resolved (6 ambiguous · 7 unresolved) — **PASS ≥80%** |
| Second `clean-junk` (dry round) | **0** removed / **0** merged |

Unresolved names in the sample are roster gaps (Parena, Lizarraga, Diñgal, …), not leftover “Looking for venue” junk. Comment count is still below Arctic’s ~61k until `enrich` pages truncated threads (old cap was 50/post).

### Code

- Arctic comments page at 100/`before` (cap 5000/thread); `enrich` refills posts whose stored count is below Reddit’s `num_comments`.
- Name cleanup: `De`/`Di`/`Del` surnames kept; trailing Email/Prerog stripped; inverted meta titles, `or`/`vs` multi-prof dumps, particle-only lasts, and comma-in-last listings rejected.
- `clean-junk` also merges duplicate roster rows. Matrix ingest no longer upserts a professor when `parse_title` fails.
- `resolve-report` skips implausible names and passes course + campus into `CRSLookup.match()`.
- ProfstoPick path is `npm run import -- --source reddit=…` (`script/import/reddit.ts`). Do not use `--source rupp=`. Matrix exports `comments_rupp_shaped.json`.

### Still open

1. **Comment backfill** — local enrich raised stored comments **55,884 → 57,935**. Arctic’s ~61k figure includes `[deleted]`/`[removed]` bodies this scraper skips. Remaining Reddit claimed-vs-stored gap is those skipped rows, not unpaged threads. `enrich` now only targets truncated threads (`stored < num_comments`), not posts that claim zero comments.
2. **Parse rate ~38%** — conversational / multi-prof / prerog posts stay unparsed (correctly excluded from the mention sample).
3. **Roster gaps** — well-parsed names missing from the CRS snapshot still show as unresolved.
4. **OAuth still optional** — listings/comments stay on Arctic Shift without Reddit credentials.

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
