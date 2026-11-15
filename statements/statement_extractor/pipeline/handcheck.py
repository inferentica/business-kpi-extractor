"""Raw rows for hand checks (evals/handcheck): for each sampled served line, the filing's own printed rows that carry
its label, with the table's header rows and scale above them, so a person can read the figure for the period off the
filing itself. Nothing here uses the extractor's readers: HTML tables are taken cell by cell with lxml, plain-text
filings line by line, PDFs by pdfplumber's own text. Rows are found by their label only, never by the served figure,
so a check cannot be steered towards agreeing.

    statement-extractor rows evals/handcheck/queue.json --out rows.md
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from ..sources.archives import archived, fetch
from ..sources.edgar import configure_identity, load_submission
from ..sources.listing import filing_for
from ..sources.retry import with_network_retries

_DOCUMENTS = ("10-K", "10-Q", "20-F", "40-F", "6-K", "EX-13", "EX-99", "ARS")
_HEADER = re.compile(r"\b(19|20)\d\d\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|thousand|million|"
                     r"billion|statements?\s+of|balance\s+sheets?|NT\$|EUR|US\$|\bUSD\b|\bNTD\b", re.I)
_FIGURE = re.compile(r"\(?\d[\d,]*\.?\d*\)?")


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _probe(label: str) -> str:
    """The label's first words, as they start a printed row (a wrapped label is joined in the served line)."""
    label = re.sub(r"\(notes?[^)]*\)", "", label, flags=re.I)
    return " ".join(_key(label).split()[:5])


def _html_tables(content: str) -> list[tuple[str, list[str]]]:
    from lxml import html as lxml_html
    # An inline XBRL document declares its encoding, which lxml refuses in a str.
    tree = lxml_html.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", content))
    found, before = [], ""
    for element in tree.iter():
        if element.tag == "table":
            rows = []
            for row in element.iter("tr"):
                cells = [re.sub(r"\s+", " ", cell.text_content()).strip() for cell in row.iter("td", "th")]
                cells = [cell for cell in cells if cell]
                if cells:
                    rows.append(" | ".join(cells))
            found.append((before[-240:], rows))
            before = ""
        elif isinstance(element.tag, str) and element.tag in ("p", "div", "font", "b", "center", "span") and element.text:
            before += " " + re.sub(r"\s+", " ", element.text)
    return found


# The statement's own title, so its table is shown before notes and analyses that print the same label.
_TITLES = {"income": r"statements?\s+of\s+(consolidated\s+)?(income|operations|earnings|profit)|income\s+statements?|"
                     r"profit\s+or\s+loss|comprehensive\s+income",
           "balance": r"balance\s+sheets?|financial\s+(position|condition)",
           "cash_flow": r"cash\s+flows?"}


def _hit(row_text: str, probes: list[str]) -> bool:
    key = _key(row_text)
    return any(key.startswith(probe) for probe in probes)


def _html_rows(content: str, probes: list[str], statement: str) -> list[str]:
    title = re.compile(_TITLES[statement], re.I)
    found = []
    for context, rows in _html_tables(content):
        hits = [row for row in rows if _hit(row, probes)]
        if not hits:
            continue
        header = [row for row in rows[:8] if not _FIGURE.fullmatch(row.split(" | ")[0]) and _HEADER.search(row)][:4]
        text = (f"context: …{context.strip()[-200:]}\n" + "\n".join(f"  H {row[:220]}" for row in header) +
                "\n" + "\n".join(f"  > {row[:260]}" for row in hits[:4]))
        found.append((not title.search(context + " ".join(header)), len(found), text))
    return [text for _, _, text in sorted(found)[:3]]


def _text_rows(text: str, probes: list[str], statement: str) -> list[str]:
    title = re.compile(_TITLES[statement], re.I)
    lines = text.splitlines()
    found = []
    for number, line in enumerate(lines):
        if not _hit(line, probes):
            continue
        header = [lines[at] for at in range(max(0, number - 70), number) if _HEADER.search(lines[at])][-7:]
        following = lines[number + 1:number + 3]  # a wrapped label's figures, on the lines after
        body = ("\n".join(f"  H {row.rstrip()[:200]}" for row in header) + "\n" +
                "\n".join(f"  > {row.rstrip()[:220]}" for row in [line, *following]))
        found.append((not title.search(" ".join(header)), len(found), body))
    return [body for _, _, body in sorted(found)[:4]]


def _documents(sample: dict) -> list[tuple[str, str, str]]:
    """(name, kind html|text, content) of the filing's printed documents."""
    accession = sample["source_accession"]
    if accession.startswith("archive:"):
        import pdfplumber
        found = []
        for item in archived(sample["symbol"]):
            if item.accession == accession:
                with pdfplumber.open(io.BytesIO(fetch(item))) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                found.append((item.url.rsplit("/", 1)[-1], "text", text))
        return found
    filing = with_network_retries(lambda: filing_for(accession))
    with_network_retries(lambda: load_submission(filing))
    found = []
    for attachment in filing.attachments:
        kind = str(attachment.document_type or "").upper()
        if not kind.startswith(_DOCUMENTS):
            continue
        content = attachment.content
        content = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or "")
        # An inline XBRL filing opens with its XBRL header, far longer than 200 KB in TSMC's and ASML's 20-Fs.
        printed = "html" if str(attachment.document).lower().endswith((".htm", ".html")) or \
            "<tr" in content[:200_000].lower() or "<html" in content[:5_000].lower() else "text"
        found.append((f"{attachment.document} ({kind})", printed, content))
    return found


def raw_rows(samples: list[dict], log=print) -> str:
    configure_identity()
    by_accession: dict[str, list[dict]] = {}
    for sample in samples:
        by_accession.setdefault(sample["source_accession"], []).append(sample)
    sections = {}
    for accession, group in by_accession.items():
        try:
            documents = _documents(group[0])
        except Exception as error:  # noqa: BLE001 - one unreadable filing never stops the rest
            documents, failure = [], f"unreadable ({type(error).__name__}: {str(error)[:100]})"
        else:
            failure = ""
        for sample in group:
            probe = _probe(sample["label"])
            probes = [probe, " ".join(probe.split()[:3])] if len(probe.split()) > 3 else [probe]
            found = []
            for name, printed, content in documents:
                reader = _html_rows if printed == "html" else _text_rows
                try:
                    rows = reader(content, probes[:1], sample["statement"]) or \
                        reader(content, probes[1:], sample["statement"])
                except Exception as error:  # noqa: BLE001 - one unparseable document never stops the rest
                    rows = [f"  (document unreadable: {type(error).__name__}: {str(error)[:80]})"]
                found.extend(f"[{name}]\n{row}" for row in rows)
            sections[sample["id"]] = (
                f"### {sample['id']}: {sample['statement']} {sample['fiscal_year']} {sample['fiscal_period']} "
                f"{sample['duration']} (ends {sample['period_end']})\n"
                f"served: {sample['label']!r} = {sample['value']:,.2f} {sample.get('currency') or ''}; "
                f"source {sample['source_form']} {accession} filed {sample['source_filed']}\n"
                + (failure or "\n".join(found[:4]) or "NO ROW FOUND for probe " + repr(probe)) + "\n")
        log(f"{accession}: {len(group)} samples, {len(documents)} documents")
    return "\n".join(sections[sample["id"]] for sample in samples)


def write_rows(sample_file: Path, out: Path) -> None:
    out.write_text(raw_rows(json.loads(sample_file.read_text())))
