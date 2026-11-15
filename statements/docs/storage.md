# Statements storage in Quotiva and R2 (Phase 7, s7-layout)

Status: **approved 2026-11-16, applied to Quotiva** (s7-gate; `label_en` added in s7-writes). Revised 2026-11-16 against the SEC's Financial Statement Data Sets and
point-in-time practice: unit, decimals, negation, taxonomy and publication time per value. The SQL below is the migration
`services/quotiva/supabase/migrations/20261116090000_statements_storage.sql` in the TradeTracker repository, with the
write gate `statements_store` (Edge Function statements-store); `20261116100000_statement_lines_label_en.sql` adds
`label_en`.

## What is stored where

| Store | What | Size (measured, round 116) |
|---|---|---|
| Quotiva Postgres | The served statements: one row per company, statement, period and basis, its lines as one JSONB value; every earlier served version; each company's split ledger and read state | about 2.4 MB of lines a company, about 0.8 MB as Postgres compresses each period's lines; about 0.4 GB for the S&P 500, 4 GB for 5,000 companies, before earlier versions |
| Cloudflare R2 (`sec-filings` bucket, `statements/v1/`) | The evidence: every column read from every filing with its checks and Arelle's verdicts, each filing's record, the company's served snapshot | about 0.84 MB a company gzipped; 0.42 GB for the S&P 500, 4.2 GB for 5,000 (the bucket holds 1.24 GB of its free 10 GB) |

One row per line would be about 10,000 rows a company (5.5 million for the S&P 500, 55 million for 5,000), past the
Pro plan's 8 GB of disk at scale; the API always reads whole statements, so lines live in one JSONB array per period.
Per-field queries belong to Phase 8's standardized tables. At 5,000 companies the served statements take about half
the plan's disk, and earlier versions add to it as companies restate: past the S&P 500, plan the extra disk (or keep
versions older than a few years in R2) before the backfill.

## Quotiva tables

```sql
-- The shape every period's lines must have (pg_jsonschema, available on Supabase).
create extension if not exists pg_jsonschema;
create function public.statement_lines_schema() returns json language sql immutable as $$ select '{
  "type": "array",
  "items": {"type": "object", "additionalProperties": false,
    "required": ["line", "key", "concept", "label", "level", "is_heading", "value", "unit"],
    "properties": {
      "line": {"type": "integer"}, "key": {"type": "string"}, "concept": {"type": "string"},
      "taxonomy": {"type": ["string", "null"]}, "member": {"type": ["string", "null"]},
      "label": {"type": "string"}, "label_en": {"type": ["string", "null"]}, "level": {"type": "integer"}, "is_heading": {"type": "boolean"},
      "negated": {"type": "boolean"}, "value": {"type": ["number", "null"]}, "printed_value": {"type": ["number", "null"]},
      "unit": {"type": ["string", "null"]}, "decimals": {"type": ["integer", "null"]},
      "split_factor": {"type": ["number", "null"]}}}}'::json $$;

-- One served statement per company, statement, period and basis: the newest presentation that passes its checks.
create table public.statement_periods (
  company_id      text not null references public.companies (company_id),
  statement       text not null check (statement in ('income', 'balance', 'cash_flow')),
  fiscal_year     text not null,
  fiscal_period   text not null check (fiscal_period in ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
  duration        text not null check (duration in ('quarter', 'ytd', 'year', 'instant')),
  basis           text not null,                 -- US GAAP, IFRS, ROC GAAP, Taiwan-IFRS, K-IFRS, ...
  is_primary      boolean not null,              -- the basis served by default; others are the newest presentation on each other basis
  period_start    date,
  period_end      date not null,
  calendar_year   smallint generated always as (extract(year from period_end)::smallint) stored,
  calendar_quarter smallint generated always as (extract(quarter from period_end)::smallint) stored,
  currency        text not null check (currency ~ '^[A-Z]{3}$'),   -- ISO 4217
  scope           text not null check (scope in ('consolidated', 'parent')),
  source          text not null,                 -- xbrl, html, text, pdf, slides, regulator form (CVM-ITR, DART-Q3, BMV-Q3, ...)
  source_accession text not null,                -- EDGAR accession, or the regulator's or archive's own id
  source_form     text not null,
  source_filed    date not null,
  available_from  timestamptz not null,          -- when it became public: the source filing's EDGAR acceptance time
                                                 -- (a regulator's publication time): the point-in-time stamp
  from_comparative boolean not null,             -- served from a later filing's comparative column
  own_accession   text,                          -- the period's own filing, when another one is served
  witness         text not null,                 -- confirmed, restated, single, contradicted, derived
  derived_from    jsonb,                         -- a derived fourth quarter: {"year": accession, "nine_months": accession}
  checks          jsonb not null,                -- the reading's checks and Arelle's verdicts, as read
  lines           jsonb not null check (jsonb_matches_schema(statement_lines_schema(), lines)),  -- see "Line fields"
  reader_version  text not null,                 -- the extractor commit that chose it
  known_from      timestamptz not null default now(),
  primary key (company_id, statement, fiscal_year, fiscal_period, duration, basis)
);
create unique index statement_periods_primary on public.statement_periods (company_id, statement, fiscal_year, fiscal_period, duration)
  where is_primary;
create index statement_periods_company_end on public.statement_periods (company_id, statement, period_end);

-- Every served version a newer one replaced: the figure first reported, then each restatement (s7-firstreported).
create table public.statement_period_versions (
  version_id      bigint generated always as identity primary key,
  like public.statement_periods excluding constraints excluding indexes,
  known_to        timestamptz not null,          -- when Quotiva stopped serving it (transaction time)
  available_to    timestamptz,                   -- when the filing that replaced it became public (point-in-time
                                                 -- end); null when no filing replaced it (a reader change, withdrawn)
  ended_by        text not null                  -- restated, newer filing, reader change, withdrawn
);
create index statement_period_versions_period on public.statement_period_versions
  (company_id, statement, fiscal_year, fiscal_period, duration, basis, known_from);

-- Each company's splits, from its own filings (selection/splits.py).
create table public.statement_splits (
  company_id      text not null references public.companies (company_id),
  first_filed     date not null,                 -- the first filing presenting the split
  ratio           numeric not null,
  share_class     text not null default '',
  primary key (company_id, first_filed, share_class)
);

-- What each company's statements were last read from, so a new filing re-reads only itself (s7-incremental).
create table public.statement_reads (
  company_id      text primary key references public.companies (company_id),
  reader_version  text not null,
  last_read_at    timestamptz not null,
  last_filed      date,
  coverage        jsonb not null,                -- the run's summary: own coverage, release quarters, golden-checked counts
  evidence_prefix text not null                  -- statements/v1/<cik>/ in R2
);

-- One row per served value, the shape of the SEC's Financial Statement Data Sets (NUM joined to PRE), for queries
-- across companies and for bulk exports.
create view public.statement_values with (security_invoker = true) as
select p.company_id, p.statement, p.fiscal_year, p.fiscal_period, p.duration, p.basis, p.is_primary,
       p.period_start, p.period_end, p.calendar_year, p.calendar_quarter, p.currency, p.scope,
       p.source_accession, p.source_form, p.available_from,
       l.line, l.key, l.concept, l.taxonomy, l.member, l.label, l.level, l.negated,
       l.value, l.printed_value, l.unit, l.decimals, l.split_factor
from public.statement_periods p,
     jsonb_to_recordset(p.lines) as l(line int, key text, concept text, taxonomy text, member text, label text,
                                      level int, is_heading boolean, negated boolean, value numeric,
                                      printed_value numeric, unit text, decimals int, split_factor numeric)
where not l.is_heading;

alter table public.statement_periods enable row level security;
alter table public.statement_period_versions enable row level security;
alter table public.statement_splits enable row level security;
alter table public.statement_reads enable row level security;
-- Writes only through the write gate (s7-gate, service role); reads through quotiva-api.
grant select, insert, update, delete on public.statement_periods, public.statement_period_versions,
  public.statement_splits, public.statement_reads to service_role;
```

## Line fields

Each line carries what the SEC's Financial Statement Data Sets carry for a value (NUM) and its place on the statement
(PRE), and what XBRL records with a fact:

| Field | Meaning | SEC data set / XBRL equivalent |
|---|---|---|
| `line` | Order on the statement | PRE `line` |
| `key` | The line's key across the company's eras (selection/identity.py) | (none: ours) |
| `concept`, `taxonomy` | The XBRL tag and its taxonomy (`us-gaap/2024`, `ifrs-full/2025`), or `printed_…` for a line read from print | NUM `tag`, `version` |
| `member` | The axis member of a breakdown line | NUM `segments` |
| `label`, `level`, `is_heading` | The filer's label, indentation, heading | PRE `plabel`, `inpth` |
| `label_en` | The English label of a line a regulator's form labels in Portuguese or Korean (CVM, DART), from its chart of accounts | (none) |
| `negated` | Shown with the tag's sign reversed (a negated label) | PRE `negating` |
| `value` | The figure as presented, in whole units, split-adjusted for per-share lines and share counts | NUM `value` |
| `printed_value`, `split_factor` | The figure as printed and the factor applied, where adjusted | (none) |
| `unit` | ISO 4217 code, `shares`, `<code>/share` or `pure` | NUM `uom`, XBRL `unitRef` |
| `decimals` | The figure's rounding as printed or tagged (`-6` for millions) | XBRL `decimals` |

The extractor's readings hold the label, level, member, sign and value; since s7-writes each reader adds what only it
knows (domain/line_fields.py): an XBRL line's unit from its fact's unit and currency, its decimals as tagged, its
taxonomy and year from the filing's schema (`us-gaap/2024`) and `negated` from a negated label; a printed line's kind
(money, per share, shares) and decimals from the scale it was read in (1,234 in millions is -6); a CVM or Mexican
line's decimals from its form's scale. A field no reader knows stays null. `available_from` is EDGAR's acceptance time
(UTC, as its filing list gives it); a source EDGAR does not time (a regulator's form, an archived report) is public
from the end of its filing day in New York, so a point-in-time query never sees it too soon.

## Time: three clocks

- **Period** (`period_start`, `period_end`, fiscal and calendar year and quarter): what the figures describe.
- **Publication** (`available_from`, `available_to`): when the filing that gives the figures became public, and when
  the one that replaced them did. A query "as known on a date" (point in time, for backtests) reads the row whose
  publication interval contains that date, so a restatement never leaks into an earlier date.
- **Serving** (`known_from`, `known_to`): when Quotiva served each version, so an audit can say what a client saw.

The current table holds the newest version; the versions table every earlier one (as first reported, each
restatement), both stamped on all three clocks, so as-reported and restated figures stay apart and nothing is
overwritten.

Writing a company: rows whose content changed are copied into `statement_period_versions` with `known_to = now()`
and an `ended_by`, then replaced; unchanged rows are left alone, so `known_from` says since when a figure has been
served. Periods no longer served are moved to the versions table with `ended_by = 'withdrawn'`.

`sec_statement_lines` stays as it is until the KPI pipeline stops writing it (s7-cutover).

## R2 evidence layout

Under `sec-filings/statements/v1/<cik>/` (a foreign filer without a CIK uses its Quotiva `company_id`):

| Key | Content |
|---|---|
| `readings/<accession>.json.gz` | Every column read from that filing, each with its lines, checks and Arelle's verdicts (the readings.jsonl rows of that accession) |
| `readings/<source>-<id>.json.gz` | The same for a regulator's form or an archived report (`CVM-ITR-162068-v1`, `DART-20260814002198`, `archive-TSM-6-K-2000-06-30`) |
| `filings.json.gz` | Every filing considered and what its reading noted |
| `served.json.gz` | The served statements as written to Quotiva, for bulk downloads and an edge cache |
| `candidate.json.gz` | A dry run's snapshot, checked against what Quotiva serves and never served |
| `summary.json.gz` | The run's summary (coverage, phases, Arelle, AI calls: 0) |

Readings are written once per filing and reader version; a reader change re-writes only the filings its era covers
(s7-versions). Nothing in R2 is served directly to clients before Phase 8's serving step.

## Approvals this design needs

1. This layout (tables and R2 keys).
2. The migration in Quotiva's project (production; applied by hand).
3. The write gate's Edge Function deploy (s7-gate) before any runner writes.
