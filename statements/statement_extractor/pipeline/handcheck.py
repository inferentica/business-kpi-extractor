"""Raw rows for hand checks (evals/handcheck): for each sampled served line, the filing's own printed rows that carry
its label, with the table's header rows and scale above them, so a person can read the figure for the period off the
filing itself. Nothing here uses the extractor's readers: HTML tables are taken cell by cell with lxml, plain-text
filings line by line, PDFs by pdfplumber's own text. Rows are found by their label only, never by the served figure,
so a check cannot be steered towards agreeing.

    statement-extractor rows evals/handcheck/sample.json --out rows.md
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
    tree = lxml_html.fromstring(content)
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


def _html_rows(content: str, probe: str) -> list[str]:
    out = []
    for context, rows in _html_tables(content):
        hits = [row for row in rows if _key(row).startswith(probe)]
        if not hits:
            continue
        header = [row for row in rows[:8] if not _FIGURE.fullmatch(row.split(" | ")[0]) and _HEADER.search(row)][:4]
        out.append(f"context: …{context.strip()[-200:]}\n" + "\n".join(f"  H {row[:220]}" for row in header) +
                   "\n" + "\n".join(f"  > {row[:260]}" for row in hits[:3]))
        if len(out) >= 3:
            break
    return out


def _text_rows(text: str, probe: str) -> list[str]:
    lines = text.splitlines()
    out = []
    for number, line in enumerate(lines):
        if not _key(line).startswith(probe):
            continue
        header = [lines[at] for at in range(max(0, number - 70), number) if _HEADER.search(lines[at])][-7:]
        following = lines[number + 1:number + 2] if not re.search(r"\d", line) else []  # figures on the next line
        out.append("\n".join(f"  H {row.rstrip()[:200]}" for row in header) + "\n" +
                   "\n".join(f"  > {row.rstrip()[:220]}" for row in [line, *following]))
        if len(out) >= 3:
            break
    return out


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
        printed = "html" if "<tr" in content[:200_000].lower() else "text"
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
            found = []
            for name, printed, content in documents:
                rows = _html_rows(content, probe) if printed == "html" else _text_rows(content, probe)
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
