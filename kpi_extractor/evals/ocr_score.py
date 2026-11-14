"""Scores each OCR reading (evals/ocr_probe.py, eval-results/ocr-probe.json) of the probe's pages against the page's truth: the figures printed on it.

A figure is a number as printed ("1,148", "(357)", "12.5%", "€11.7"), compared by its digits and decimal point
("1148", "357", "12.5", "11.7"), with its sign (parentheses or a minus) and its percent sign checked apart. Lone
single digits (footnote marks, page numbers, list numbers) are left out on both sides."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

NUMBER = re.compile(r"(\()?([-−–]?)(?:US\$|R\$|NT\$|[$€£¥₹])?\s?(?<![A-Za-z\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![A-Za-z\d])(\))?\s?(%)?")


def figures(text: str) -> list[tuple[str, bool, bool]]:
    """(digits, negative, percent) of every figure in a text."""
    out = []
    for match in NUMBER.finditer(text):
        paren, minus, body, close, pct = match.groups()
        digits = re.sub(r"[,  ]", "", body)
        if re.fullmatch(r"\d", digits) and not pct:
            continue
        out.append((digits.lstrip("0") or "0" if "." not in digits else digits, bool(paren and close) or bool(minus), bool(pct)))
    return out


def deepseek_text(pages: list[dict] | None) -> str:
    """DeepSeek's raw page output without its layout tokens and their box coordinates."""
    text = "\n".join(page.get("text") or "" for page in pages or [])
    text = re.sub(r"<｜box｜>.*?<｜/box｜>", " ", text, flags=re.S)
    text = re.sub(r"<｜[^｜]*｜>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)  # HTML table tags, if any
    return text


def rapid_text(reading: dict | None) -> str:
    return "\n".join(word[4] for word in (reading or {}).get("words") or [])


def score(truth: list[tuple[str, bool, bool]], read: list[tuple[str, bool, bool]]) -> dict:
    want = Counter(digits for digits, _neg, _pct in truth)
    got = Counter(digits for digits, _neg, _pct in read)
    found = sum(min(count, got[d]) for d, count in want.items())
    extra = sum(max(0, count - want[d]) for d, count in got.items())
    # A sign or percent read differently on a figure found
    truth_sign = Counter((d, neg) for d, neg, _ in truth)
    read_sign = Counter((d, neg) for d, neg, _ in read)
    sign_ok = sum(min(count, read_sign[key]) for key, count in truth_sign.items())
    return {"truth": sum(want.values()), "found": found, "missed": sum(want.values()) - found, "extra": extra,
            "sign_ok": sign_ok, "missing": sorted((want - got).elements()), "unexpected": sorted((got - want).elements())}


def classify(missing, unexpected):
    """Pairs each figure read but not on the page with a missed figure: a separator error (same digits, the comma read
    as a point or the reverse), a digit error (one digit different, same length), or neither (an unpaired extra)."""
    miss = list(missing); out = Counter(); pairs = []
    for u in unexpected:
        ud = u.replace(".", "")
        hit = next((m for m in miss if m.replace(".", "") == ud and m != u), None)
        kind = "separator"
        if hit is None:
            hit = next((m for m in miss if len(m) == len(u) and sum(a != b for a, b in zip(m, u)) == 1), None)
            kind = "digit"
        if hit is None:
            out["unpaired"] += 1; continue
        miss.remove(hit); out[kind] += 1; pairs.append((kind, hit, u))
    return out, pairs


def main(results_path: str, truth_path: str) -> None:
    results = {page["id"]: page for page in json.load(open(results_path))["pages"]}
    truth = json.load(open(truth_path))
    readers = {"rapidocr": lambda p: rapid_text(p.get("rapidocr")), "rapidocr_x2": lambda p: rapid_text(p.get("rapidocr_x2")),
               "deepseek_text": lambda p: deepseek_text((p.get("deepseek_text_recognition") or {}).get("pages")),
               "deepseek_layout": lambda p: deepseek_text((p.get("deepseek_rich_layout_recognition") or {}).get("pages"))}
    rows = []
    for page_id, page in results.items():
        if page_id not in truth:
            continue
        want = figures(truth[page_id]["text"])
        row = {"id": page_id, "source": truth[page_id]["source"]}
        for name, read in readers.items():
            row[name] = score(want, figures(read(page)))
        rows.append(row)
    json.dump(rows, open("eval-results/ocr-scores.json", "w"), indent=1)
    total = {name: Counter() for name in readers}
    for row in rows:
        for name in readers:
            total[name].update({key: row[name][key] for key in ("truth", "found", "missed", "extra", "sign_ok")})
            total[name].update(classify(row[name]["missing"], row[name]["unexpected"])[0])
    for name, t in total.items():
        print(f"{name:16} found {t['found']}/{t['truth']} ({t['found'] / max(1, t['truth']):.1%})  digit errors {t['digit']}  "
              f"separator errors {t['separator']}  other extras {t['unpaired']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval-results/ocr-probe.json", sys.argv[2] if len(sys.argv) > 2 else "evals/ocr_truth.json")
