# Statements storage in Quotiva and R2 (Phase 7, s7-layout)

Status: **design for the owner's approval.** Nothing here is applied. The SQL below becomes a migration in
`services/quotiva/supabase/migrations/` only after approval, and is applied to Quotiva's project by hand.

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
  currency        text not null,
  scope           text not null check (scope in ('consolidated', 'parent')),
  source          text not null,                 -- xbrl, html, text, pdf, slides, regulator form (CVM-ITR, DART-Q3, BMV-Q3, ...)
  source_accession text not null,                -- EDGAR accession, or the regulator's or archive's own id
  source_form     text not null,
  source_filed    date not null,
  from_comparative boolean not null,             -- served from a later filing's comparative column
  own_accession   text,                          -- the period's own filing, when another one is served
  witness         text not null,                 -- confirmed, restated, single, contradicted, derived
  derived_from    jsonb,                         -- a derived fourth quarter: {"year": accession, "nine_months": accession}
  checks          jsonb not null,                -- the reading's checks and Arelle's verdicts, as read
  lines           jsonb not null,                -- [{line, key, concept, member, label, level, is_heading, value, printed_value, split_factor}]
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
  known_to        timestamptz not null,
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

alter table public.statement_periods enable row level security;
alter table public.statement_period_versions enable row level security;
alter table public.statement_splits enable row level security;
alter table public.statement_reads enable row level security;
-- Writes only through the write gate (s7-gate, service role); reads through quotiva-api.
grant select, insert, update, delete on public.statement_periods, public.statement_period_versions,
  public.statement_splits, public.statement_reads to service_role;
```

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
| `summary.json.gz` | The run's summary (coverage, phases, Arelle, AI calls: 0) |

Readings are written once per filing and reader version; a reader change re-writes only the filings its era covers
(s7-versions). Nothing in R2 is served directly to clients before Phase 8's serving step.

## Approvals this design needs

1. This layout (tables and R2 keys).
2. The migration in Quotiva's project (production; applied by hand).
3. The write gate's Edge Function deploy (s7-gate) before any runner writes.
