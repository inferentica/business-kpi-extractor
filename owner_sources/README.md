# How new quarters reach hand-built stocks

Hand-built companies (`business_kpi_specs.model = 'owner'`) are no longer read by the ordinary pipeline. When the
scheduled refresh reaches one, it takes the owner-locked path (`kpi_extractor/owner/`):

1. **Nothing is rebuilt.** No new list version, no new metric, group or line; names and order stay as you left them. The
   AI is shut for the whole run: an AI or OCR call fails it (`owner/guard.py`). The database refuses another model's list
   over an owner list, and any change to its groups or names that is not your own edit (`business_kpi_owner_spec_guard`).
2. **Only the new period.** A filing whose period is the quarter after the newest stored one adds that quarter, and a 10-K
   adds the year's FY rows and Q4 = FY less the Q3 report's nine months. A period with any owner-decided row is
   read-only: nothing in it is inserted, updated, re-flagged or rejected.
3. **Each line from its stored source, by code.** `business_kpi_specs.sources` holds, per line, its release table cell
   (anchor text, row label, the quarter's column by its header date, scale) and/or its 10-Q/10-K source (XBRL concept,
   exact member set, wrapper members ignored; or a report table cell). Per group it holds the total the lines add up to.
4. **Verified only when proven.** A figure is verified when its source gives exactly one figure and its table adds up to
   its total. Two different figures, or a total that cannot be read, write it held, with the reason.
5. **A basis change builds nothing.** A new segment member, a breakdown line gone, lines that no longer add up, or the table
   gone or rescaled: the table is not written for that period, and it goes to your hand pass.
6. **Release first, then the report.** The release fills the quarter the day it is filed; when the 10-Q/10-K follows, it
   replaces only the release figures the refresh wrote for that newest quarter, only where the two differ, with a note.
   An owner-decided figure the report gives differently is never changed: it goes to the hand pass as "differs".

## The hand-reading queue

Whatever code could not place goes to `business_kpi_hand_reads`, shown in Quotiva Review's exceptions as **To read**
(one case per company and quarter, each line with why and a link to its filing):

| kind | meaning | what to do |
| --- | --- | --- |
| hand | a line only a person reads (Dell's AI orders and backlog, a figure in the CEO's comment) | read it, store it confirmed |
| unmapped | no source is stored for the line | read it; add its source to `<SYMBOL>.json` so code reads it next time |
| missing | the line is not where its source points | read it, or mark it not reported (never marked N/A automatically) |
| basis | a basis change; nothing was built for that table | rebuild the period by hand, then update the line sources |
| differs | a later filing gives an owner-decided figure differently | decide which stands |
| gap | the filing is not the next quarter (a quarter skipped, a calendar change) | build the missing period by hand |

An entry leaves the Review list once its line has an owner-decided figure or a not-reported mark for that period; an
entry for a whole table (basis, gap) stays until it is closed (`status = 'done'`).

## Line sources: files and backfill

`owner_sources/<SYMBOL>.json` is each company's mapping (schema: `kpi_extractor/owner/sources.py`).
`python -m kpi_extractor.owner.backfill --state owner_state.json` derives them: for each line it finds the XBRL fact or
table cell equal to your stored figure in that figure's own filing, keeps it only if the same source gives your stored
figure in the recent quarters checked, then replays the newest quarter through the refresh and compares it with what you
stored. It reads SEC only (no AI) and writes nothing to the database: it writes the files, `backfill_report.json` (lines it
could not map, and each company's replay) and `backfill.sql`, which you apply with your Allow. A mapping written by hand
(DELL's) is checked, never replaced.

`owner_state.json` comes from a read-only query:

```sql
select json_build_object(
  'specs', (select json_agg(s) from (select symbol, company_id, version, model from business_kpi_specs
                                     where status = 'active' and model = 'owner') s),
  'values', (select json_agg(v) from (select v.* from business_kpi_values v join business_kpi_specs s
             on s.company_id = v.company_id and s.status = 'active' and s.model = 'owner'
             where v.validation_status <> 'rejected' and v.owner_decision is not null and v.period_end >= '2024-09-01') v));
```

A hand fix to an owner list's groups or names sets `owner_edited_at = now()` in the same statement, or the guard refuses it.

## The gap finder

The refresh's second job finds; it never writes a figure (`kpi_extractor/owner/finder.py`, no AI). For each new 10-Q or
10-K of a hand-built company it compares the filing's XBRL with the stored owner tables and lists, in
`business_kpi_gap_findings` (not in the figures, not in Review):

| kind | meaning |
| --- | --- |
| untracked | an axis's figures no owner table carries (Dell's Products/Services, its revenue by country) |
| missing_line | a member of an owner table's own axes the table has no line for |
| missing_quarter | a quarter missing inside an owner line's stored run (the window is never extended before its first quarter) |
| differs | a filed figure for a stored line and period that is not the stored one, with both |
| q4_basis | a 10-K's lines and its Q3 report's nine months differ, so Q4 = FY less nine months would mix bases |

Each finding keeps the filed value, concept, members, accession and period. A filed figure equal to one already stored
for the same period, under any key, is a repeat and is not listed. Operating income counts only by reportable segment.
Nothing found is added until you check it against its filing and allow it.

A one-off sweep across all hand-built companies reads SEC only and writes two local files:

```bash
python -m kpi_extractor.owner.finder --state owner_state.json --out findings.json --sql findings.sql
```

`findings.sql` inserts the findings into `business_kpi_gap_findings`; run it only with your Allow.
