# Tariff registry

Chapter 99 measures recorded from primary sources (decisions.md D-026). The timeline CSVs (`config/tariff_timelines/`) are still each study's own input; this is the ledger they are checked against.

| File | One row is |
|---|---|
| `sources.csv` | one primary source that was read: a Federal Register document (document number, citation, publication date, signing date, URL, SHA-256 of the text) or an HTS archive file (edition, URL, SHA-256 of the PDF) |
| `measures.csv` | one dated change: date, `date_type` (announced / signed / effective / ended), chapter 99 heading or range, change, origins, `products` (all = every product unless coverage lists an exception; list = only the products in coverage), source ids, HTS note, `status` |
| `coverage.csv` | one measure against one HTS6: covered / exempt / not_covered / suspended, with the HTS note that shows it |

Rules:

- `status = verified`: the date, the change and the heading were all read in the listed sources. `待核对` (to verify): at least one of them was not; `notes` says which.
- `ended` is the last day the measure applies (for example 9903.03.01 expires at the end of 2026-07-23).
- Only chapter 99 measures are recorded. MFN base rates are not, so a timeline's base-rate row is always `not_in_registry` in `registry-check`.
- Sources may only be hts.usitc.gov, federalregister.gov, ustr.gov or cbp.gov; loading checks this. A new source site needs the user's permission to read it.
- The heading tables in the chapter 99 PDFs (the rate cells) do not extract as text, so rates come from the Federal Register text or the wording of the HTS notes.

Usage (read-only, never edits a timeline):

```powershell
python run.py registry-check                                  # validate the three tables, list what is still to verify
python run.py registry-check --study config\studies\<id>.toml # compare this study's timeline with the registry
```

Verdicts: `match` (same date, origins and headings agree), `date_differs` (headings and origins agree, the date differs), `not_in_registry`, `missing_from_timeline` (in the registry, reaches this scope's HTS6, not in the timeline; exemptions included), `before_study_period`, `checked_not_covered`. Editing a timeline changes `study_sha256` and needs a new analysis run, so ask the user first.

Built on 2026-11-08 from 27 sources (25 Federal Register documents and 2 chapter 99 editions: 2025 Rev32 and 2026 Rev22), with 22 measures and 49 coverage rows (8517.13 smartphones, 8471.30 laptops, 8507.60 lithium-ion batteries). The `notes` columns are in Chinese.
