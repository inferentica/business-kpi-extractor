"""DeepSeek's OCR against the extractor's local OCR model (RapidOCR), page by page, before either is trusted with more.

Each page of evals/ocr_probe.json (an image of a filing: a slide, a scanned report, a chart) is read by RapidOCR as the
chart reader uses it (as is, and enlarged with its contrast stretched) and by DeepSeek's OCR API with both of its
purposes. Nothing is scored here: the readings are saved to eval-results/ocr-probe.json and checked against figures
read off the pages by hand (evals/ocr_truth.json). Nothing is stored."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from ..ops.control import ControlPlane
from ..readers.charts import ocr_words
from ..steps.chart_slides import _fetch_image

PURPOSES = ("text_recognition", "rich_layout_recognition")


def _words(words) -> list[list]:
    return [[round(word.x0), round(word.y0), round(word.x1), round(word.y1), word.text] for word in words]


def read_page(plane: ControlPlane, page: dict) -> dict:
    data = _fetch_image(page["url"])
    result: dict = {**page, "bytes": len(data)}
    for name, read in (("rapidocr", lambda: ocr_words(data)), ("rapidocr_x2", lambda: ocr_words(data, (0, 0), 2))):
        started = time.monotonic()
        try:
            result[name] = {"words": _words(read()), "seconds": round(time.monotonic() - started, 2)}
        except Exception as error:  # noqa: BLE001 - a failure is a result of the probe
            result[name] = {"error": str(error)[:300]}
    filename = page["url"].rsplit("/", 1)[-1]
    for purpose in PURPOSES:
        started = time.monotonic()
        try:
            reply = plane.ocr(page["symbol"], purpose, filename, data)
            result[f"deepseek_{purpose}"] = {"pages": reply.get("pages"), "usage": reply.get("usage"), "costCny": reply.get("costCny"),
                                             "seconds": round(time.monotonic() - started, 2), "callId": reply.get("callId")}
        except Exception as error:  # noqa: BLE001
            result[f"deepseek_{purpose}"] = {"error": str(error)[:300]}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="evals/ocr_probe.json")
    args = parser.parse_args(argv)
    pages = json.loads(Path(args.manifest).read_text())["pages"]
    plane = ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    symbols = sorted({page["symbol"] for page in pages})
    plane.run_id = plane.call("start", runId=None, triggerType="eval", symbols=symbols, quarters=1, forceRefresh=True)["runId"]
    results = []
    for page in pages:
        results.append(read_page(plane, page))
        last = results[-1]
        print(f"{page['id']}: rapidocr {len(last['rapidocr'].get('words') or [])} words; deepseek "
              + ", ".join(f"{purpose} {'error' if 'error' in last[f'deepseek_{purpose}'] else len(last[f'deepseek_{purpose}']['pages'] or [])}"
                          for purpose in PURPOSES), flush=True)
    out = Path("eval-results")
    out.mkdir(exist_ok=True)
    (out / "ocr-probe.json").write_text(json.dumps({"pages": results}, indent=1))
    cost = sum((page.get(f"deepseek_{purpose}") or {}).get("costCny") or 0 for page in results for purpose in PURPOSES)
    plane.call("finish", summary={"ocrProbe": {"pages": len(results), "costCny": round(cost, 4)}})
    print(f"{len(results)} pages, DeepSeek OCR ¥{cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
