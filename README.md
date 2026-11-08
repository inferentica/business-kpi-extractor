# Business KPI extractor

Reads business segments and operating KPIs (for example TSMC's revenue by node or Meta's daily active people) from
SEC filings for TradeTracker and Bedrock. It runs on GitHub-hosted runners (`.github/workflows/extract.yml`), started
by TradeTracker's Supabase project: a daily cron for every tracked company, and the Refresh button for one company.
The runner holds no secrets: it signs in to the `business-kpi-workflow` Edge Function with GitHub OIDC, and that
function stores values and calls DeepSeek. Every AI call is DeepSeek Flash: plain for quick tasks, with reasoning at
high effort (**the reasoning reader**) for the ones that decide. Each call's reply, prompt version and run are logged,
and each figure keeps the ids of the calls that read, reviewed or explained it.

## How values are found

The AI decides and points; code reads and proves. No number typed by an AI is ever stored.

1. **Official breakdowns from XBRL.** Each 10-Q, 10-K and 20-F is read with edgartools. Revenue facts tagged with one
   segment, product or geography dimension become breakdowns (a dimension that only ever says "the whole company",
   like Netflix's Streaming, is ignored). Parent rows and overlapping rows are dropped, members are matched across
   filings by their official label, and a breakdown is kept only if it adds up to reported revenue within 0.1%. When
   the rules cannot reconcile a plausible breakdown, **the reasoning reader picks its rows**, and they must add up exactly.
   Q4 is the year minus Q1–Q3.
2. **Which filings are earnings documents.** 8-K Item 2.02 exhibits are earnings releases. Other furnished filings
   after a quarter end (every foreign filer's 6-K) are **classified by Flash**: earnings release, financial report,
   presentation, or unrelated.
3. **A KPI list and names per company.** Once, **the reasoning reader** reads the latest quarter's documents (a foreign
   filer's two latest, so its report is seen even between a release and the report) and
   proposes the KPIs XBRL does not cover (mixes such as revenue by node, operating metrics, quarterly breakdowns for
   foreign filers) and names every breakdown professionally ("Revenue by Market Platform"); a group repeating an XBRL
   breakdown is dropped. A list holds at most 8 groups and about 24 KPIs; past that, operating metrics go from the end
   of their group. Every new quarter, **the reasoning reader audits** the documents against the list and the values
   captured, adding KPIs the company now reports (a new node, a new segment) and retiring ones it stopped reporting,
   but only after three quarters without a figure. A list the owner edited is never retired from. Existing keys never
   change meaning.
4. **Reading earnings documents.** Documents become numbered tables and text blocks; long reports are narrowed to the
   sections **Flash picks** from their outline. **Flash** points to each KPI's cell or quote and names the row label
   it read; code checks the label against the row, and the column header's own date against the quarter. Its reading
   stands alone only when every KPI is where it was last quarter, no value repeats an earlier period's number (the
   tell of a prior-year column) and every check passes. Otherwise **the reasoning reader** reads independently:
   values stand where the two agree, and it reviews the rest, picking one of the two readings or neither, with a
   reason.
5. **Checks before serving.** Mixes add up to about 100%, breakdowns match their total within 1%, the period matches
   the filing, amounts use the scale the table, column or document declares (a scale only the AI gives must be
   quoted from the document), and a sharp change against last quarter is flagged. **The reasoning reader explains** a
   flagged change, shown the previous figure; it stands only with a sentence found in the document that gives a
   cause, not one that restates the figure.
   Anything unresolved is stored as `needs_review` and never served.

Prompts share one system message and put the document first, so every task on the same document after the first
hits DeepSeek's context cache.

## Accuracy

`evals/golden.json` holds hand-verified values (XBRL, derived Q4, TSMC's quarterly report tables, release KPIs). The
`Evaluate` workflow runs the real pipeline and AI against them without writing to production and reports correct,
wrong and missing values. Run it after any change to prompts, models or rules. With `suite=prompts` it scores each
prompt on its own against `evals/prompts.json` (Review against planted wrong cells, Explain against planted false
jumps, Select against the tables known figures came from); with `lists=fresh` it also scores the lists Propose writes
against the ones the owner edited, and counts groups the owner removed that come back (`evals/removed_groups.json`).

## Layout

- `kpi_extractor/`: `sec.py` (filings), `xbrl.py`, `document.py` (tables, text, numbers, outlines), `ai.py` (tasks,
  prompts, schemas), `extract.py` (reading), `derive.py` (Q4 and full years), `cli.py` (one run), `evaluate.py`
  (golden set).
- `kpi_extractor/pipeline.py` holds the order of one company's run. Its steps are in `kpi_extractor/steps/`: `loading.py`,
  `reports.py` (XBRL), `financial_statements.py`, `chart_slides.py`, `release_tables.py`, `tagged_kpis.py`,
  `listing_history.py`, `kpi_list.py`, `release_flow.py`, `ai_reading.py`, `series_identity.py`, `series_checks.py` and
  `shared.py`.
- `kpi_extractor/quality/checks.py` holds the checks on figures. Each declares what it applies to and answers with findings; the
  caller decides what comes of them. `audit_stored` reports what a wider check would say about the figures now served.
- `kpi_extractor/store.py` is the one way to change a company's figures and filings. Steps read them read-only and hand
  back the rows they want written; `tests/test_store.py` keeps it that way.
- Control plane and storage live in the TradeTracker repository: `supabase/functions/business-kpi-workflow`, tables
  `business_kpi_runs`, `business_kpi_specs`, `business_kpi_filings`, `business_kpi_values`.

## Running

```bash
pip install '.[test]' && pytest
python -m kpi_extractor --dry-run --symbols NVDA,TSM --quarters 8     # SEC data only, no AI, no writes
gh workflow run extract.yml -f symbols=TSM -f quarters=20             # production run for one company
gh workflow run evaluate.yml                                          # score against the golden set
```

## Verifying a change before it is pushed

Every change passes the regression net first, in one command:

```bash
python -m kpi_extractor.evals.verify --trial eval-results/trial.json --remote
```

1. All tests, by their real exit code.
2. The rule replay (`kpi_extractor.evals.rule_replay`): each figure the last trial accepted is read again from its own filing with
   the current code, no AI, and every figure whose value or status would change is listed. Each change must be one the
   change meant.
3. With `--remote`, the 88-value golden set and the hand-audited set (`evals/audited.json`) on GitHub.

A 50-company trial is a confirmation, not a test: rerun one only after the net passes, with `lists_from` set to an
earlier trial's run id so the rerun measures the readers alone, not a newly written list.

