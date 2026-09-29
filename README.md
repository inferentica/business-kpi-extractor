# Business KPI extractor

Reads business segments and operating KPIs (for example TSMC's revenue by node or Meta's daily active people) from
SEC filings for TradeTracker and Bedrock. It runs on GitHub-hosted runners (`.github/workflows/extract.yml`), started
by TradeTracker's Supabase project: a daily cron for every tracked company, and the Refresh button for one company.
The runner holds no secrets: it signs in to the `business-kpi-workflow` Edge Function with GitHub OIDC, and that
function stores values and calls DeepSeek.

## How values are found

The AI decides and points; code reads and proves. No number typed by an AI is ever stored.

1. **Official breakdowns from XBRL.** Each 10-Q, 10-K and 20-F is read with edgartools. Revenue facts tagged with one
   segment, product or geography dimension become breakdowns (a dimension that only ever says "the whole company",
   like Netflix's Streaming, is ignored). Parent rows and overlapping rows are dropped, members are matched across
   filings by their official label, and a breakdown is kept only if it adds up to reported revenue within 0.1%. When
   the rules cannot reconcile a plausible breakdown, **DeepSeek Pro picks its rows**, and they must add up exactly.
   Q4 is the year minus Q1–Q3.
2. **Which filings are earnings documents.** 8-K Item 2.02 exhibits are earnings releases. Other furnished filings
   after a quarter end (every foreign filer's 6-K) are **classified by Flash**: earnings release, financial report,
   presentation, or unrelated.
3. **A KPI list and names per company.** Once, **Pro** (reasoning first) reads the latest quarter's documents and
   proposes the KPIs XBRL does not cover (mixes such as revenue by node, operating metrics, quarterly breakdowns for
   foreign filers) and names every breakdown professionally ("Revenue by Market Platform"). Every new quarter, **Pro
   audits** the documents against the list and the values captured, adding KPIs the company now reports (a new node,
   a new segment) and retiring ones it stopped reporting. Existing keys never change meaning.
4. **Reading earnings documents.** Documents become numbered tables and text blocks; long reports are narrowed to the
   sections **Flash picks** from their outline. **Flash** points to each KPI's cell or quote. Its reading stands alone
   only when every KPI is where it was last quarter, no value repeats an earlier period's number (the tell of a
   prior-year column) and every check passes. Otherwise **Pro** reads independently: values stand where the two
   agree, and Pro reviews the rest, picking one of the two readings or neither.
5. **Checks before serving.** Mixes add up to about 100%, breakdowns match their total within 1%, the period matches
   the filing, amounts use the scale the table or document declares, and a sharp change against last quarter is
   flagged. **Pro explains** a flagged change; it stands only with a supporting sentence found in the document.
   Anything unresolved is stored as `needs_review` and never served.

Prompts share one system message and put the document first, so every task on the same document after the first
hits DeepSeek's context cache.

## Accuracy

`evals/golden.json` holds hand-verified values (XBRL, derived Q4, TSMC's quarterly report tables, release KPIs). The
`Evaluate` workflow runs the real pipeline and AI against them without writing to production and reports correct,
wrong and missing values. Run it after any change to prompts, models or rules.

## Layout

- `kpi_extractor/`: `sec.py` (filings), `xbrl.py`, `document.py` (tables, text, numbers, outlines), `ai.py` (tasks,
  prompts, schemas), `extract.py` (reading and checks), `derive.py` (Q4 and full years), `pipeline.py` (one company),
  `cli.py` (one run), `evaluate.py` (golden set).
- Control plane and storage live in the TradeTracker repository: `supabase/functions/business-kpi-workflow`, tables
  `business_kpi_runs`, `business_kpi_specs`, `business_kpi_filings`, `business_kpi_values`.

## Running

```bash
pip install '.[test]' && pytest
python -m kpi_extractor --dry-run --symbols NVDA,TSM --quarters 8     # SEC data only, no AI, no writes
gh workflow run extract.yml -f symbols=TSM -f quarters=20             # production run for one company
gh workflow run evaluate.yml                                          # score against the golden set
```
