# HTS sources for lithium-ion v2

Chapter 85 PDFs from the HTS archive, downloaded 2026-11-06 with the user's OK. URL pattern: `https://hts.usitc.gov/reststop/file?release=<release>&filename=Chapter%2085`. Release start dates are from `https://hts.usitc.gov/reststop/releaseList`.

| file | edition | in force from | .10 (EV) | .20 (other) | .30 (storage) | .90 (other) | SHA-256 |
|---|---|---|---|---|---|---|---|
| `chapter85_basicCorrections2.pdf` | 2017 Basic | 2017-01-01 | yes | yes | no | no | `1db15f8080680cd46a5b103afdeb86b0eb5ca72fa5b8dd3011fc3e0241e73c99` |
| `chapter85_2022HTSABasic.pdf` | 2022 Basic | 2022-01-01 | yes | yes | no | no | `56a22363a9ec9da97793601ab858531cc3cd5dc7d9d7e30b68438f128f65b145` |
| `chapter85_2024HTSBasic.pdf` | 2024 Basic | 2024-01-01 | yes | yes | no | no | `c18e3b2a9fde42dc020311cc19cc8ab57bc6cad85bec9bdc58c222dff6976d6c` |
| `chapter85_2025HTSBasic.pdf` | 2025 Basic | 2025-01-01 | yes | yes | no | no | `4df0f253b35b96c64ed01c4fc5eb0d0aa1f76bf4a7d369dee3fd3a3bc7c2eb0b` |
| `chapter85_2026HTSBasic.pdf` | 2026 Basic | 2025-12-31 | yes | yes | no | no | `9d56264bd9438f45a4bfeab5ddde7ab241e4c3bfa8e310faa3d7b79025aa6a8f` |
| `chapter85_2026HTSRev5.pdf` | 2026 Revision 5 | 2026-04-08 | yes | no | yes | yes | `415fa7fc931c2e41f28cb1f5bd0124d08b3022405a3e16dd05df0f2fb54d3bf8` |
| `chapter85_2026HTSRev10.pdf` | 2026 Revision 10 | 2026-06-08 | yes | no | yes | yes | `dc9095e314e1d7674ac3a35a2f83523671fad476bcca93ac6a8e712bf121a518` |
| `chapter85_2026HTSRev11.pdf` | 2026 Revision 11 | 2026-07-01 | yes | no | yes | yes | `6963bf2ab21ec36d4863650cb4671bb995c2759a2f151667eaa4b19c4233da27` |

All lines print units `No.` and `kg`. The .10 text is the same in every file. The .20 -> .30 + .90 split took effect between 2026-01-16 (Rev1) and 2026-04-08 (Rev5); Rev1-Rev4 were not checked. Statistical note 2 (in Rev5 and later) defines a battery energy storage system as lithium-ion modules plus management circuitry, at least 1 kWh, fully encased in a housing.

Not checked: the 2018-2021 and 2023 editions and mid-year revisions, so HTS continuity stays unverified (decisions.md D-015). These files are not an hts-history catalog.

## Chapter 99 (added 2026-11-07, for the tariff timeline)

Downloaded 2026-11-07 from the HTS archive (URL pattern above, `filename=Chapter%2099`). Text was pulled by a stdlib script and searched for `8507.60` and `9903`; the rate cells of the heading table did not extract, so rates in `config/tariff_timelines/*.csv` that are not in a note's text are marked as coming from the user's notes.

| file | edition | in force from | what it shows for 8507.60 | SHA-256 |
|---|---|---|---|---|
| `chapter99_2025HTSRev32.pdf` | 2025 Revision 32 | (last 2025 revision) | note 20(r): 9903.88.15 (List 4A) is 7.5%; note 20(s)(6): 9903.88.15 covers 8507.60.00 except .0010; note 31(b)(4): 9903.91.01 covers 8507.60.0010 from 2024-09-27; note 2(s)/(u): 9903.01.20 from 2025-02-04 to before 2025-03-04, 9903.01.24 from 2025-11-10 | `2c03f410b370127eca27db9188f9d37d12de7709069204602b693d4036d11509` |
| `chapter99_2026HTSRev22.pdf` | 2026 Revision 22 | 2026-10-30 (current on 2026-11-07) | note 31(g)(5): 9903.91.06 covers 8507.60.00 from 2026-01-01; note 31(b) items (1)-(4) deleted; note 2(v)(xviii): 9903.01.43-.62 and .64-.76 terminated as of 2025-08-07 (see 9903.02.02-.73), 9903.01.63 suspended; note 2(aa): 9903.03.01-.11 expired at the close of 2026-07-23 (91 Fed. Reg. 9339) | `290eee3d064837a003c8ee7daffebb8f5c44cdd96af4349fed40851f5c317fc2` |

URLs: `https://hts.usitc.gov/reststop/file?release=2025HTSRev32&filename=Chapter%2099`, `https://hts.usitc.gov/reststop/file?release=2026HTSRev22&filename=Chapter%2099`.

## Current HTS structure (added 2026-11-07, for the drill-down menu)

Response of the HTS REST search the scope resolver uses, saved as returned on 2026-11-07 (current edition 2026 Rev22). It lists 8507.60.00 (general rate 3.4%) and its statistical suffixes .10, .30 and .90 (units No., kg); footnotes and additionalDuties are empty for these lines, so chapter 99 coverage comes from the chapter 99 notes above. Read by `tables/drilldown_menu.csv` via the study's `[drilldown] structure`.

| file | URL | SHA-256 |
|---|---|---|
| `hts_rest_search_8507.60.json` | `https://hts.usitc.gov/reststop/search?keyword=8507.60` | `31e17d196244c3e56277ada5255ab79f265893c00f6303d17d4d537304688cc9` |
