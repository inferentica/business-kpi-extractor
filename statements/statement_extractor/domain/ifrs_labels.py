"""English names of IFRS taxonomy elements, from the IFRS Foundation's own label files. The files are fetched on the
runner into the run's cache and never kept in the repository: the Foundation limits their reproduction. An element
named in neither version read has no English name here."""
from __future__ import annotations

import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# The 2024 taxonomy (2025's label file leaves out core elements such as Revenue), and one from 2018 for elements since retired (OpenDART's reports from 2015 tag with them).
VERSIONS = ("2024-03-27", "2018-03-16")
_URL = "https://xbrl.ifrs.org/taxonomy/{v}/full_ifrs/labels/lab_full_ifrs-en_{v}.xml"
_XLINK = "{http://www.w3.org/1999/xlink}"
_STANDARD = "http://www.xbrl.org/2003/role/label"


def parse_labels(content: bytes) -> dict[str, str]:
    """Each element's standard English label: "Revenue" for ifrs-full_Revenue."""
    root = ET.fromstring(content)
    located, texts, names = {}, {}, {}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "loc":
            located[element.get(_XLINK + "label")] = element.get(_XLINK + "href", "").rsplit("#", 1)[-1]
        elif tag == "label" and element.get(_XLINK + "role") == _STANDARD:
            texts[element.get(_XLINK + "label")] = (element.text or "").strip()
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "labelArc":
            name, text = located.get(element.get(_XLINK + "from")), texts.get(element.get(_XLINK + "to"))
            if name and text:
                names[name.removeprefix("ifrs-full_")] = text
    return names


def english_names() -> dict[str, str]:
    """Element name (without "ifrs-full_") to English label, newest version first; empty where nothing can be read."""
    folder = os.environ.get("STATEMENTS_ARCHIVE_CACHE")
    names: dict[str, str] = {}
    for version in reversed(VERSIONS):
        kept = Path(folder, "regulators", f"ifrs-labels-{version}.json") if folder else None
        try:
            if kept is not None and kept.exists():
                found = json.loads(kept.read_text())
            else:
                request = urllib.request.Request(_URL.format(v=version), headers={"User-Agent": "TradeTracker statements"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    found = parse_labels(response.read())
                if kept is not None:
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    kept.write_text(json.dumps(found))
        except (OSError, ET.ParseError):
            continue
        names.update(found)
    return names
