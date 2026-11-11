"""The company's own names, and the names its filings print, compared: a 6-K's cover registrant, the company a
statement's title names, the issuer a release opening names. Another registrant's report, an acquired company's or a
listed subsidiary's statements, and another issuer's release furnished in the company's 6-K are never its own."""
from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher

# "(Exact Name of the Registrant as Specified in the Charter)" on América Móvil's covers.
_REGISTRANT = re.compile(r"\(\s*(?:Translation\s+of\s+(?:the\s+)?registrant|Exact\s+name\s+of\s+(?:the\s+)?registrant)", re.I)
_BEFORE_NAME = re.compile(r".*(?:\d{1,3}-\d{4,6}|\b(?:19|20)\d{2}\b|\b1934\b|\bForm\s+6-K\b)\s*:?", re.I | re.S)
_SUFFIXES = {"THE", "PLC", "INC", "CORP", "CORPORATION", "LTD", "LIMITED", "CO", "COMPANY", "SA", "AG", "NV", "LLC", "ON"}
_NAMES: dict[str, list[str]] = {}


def _words(name: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", html.unescape(name)).encode("ascii", "ignore").decode()  # "PETRÓLEO"
    return {word for word in re.sub(r"[^A-Z0-9]+", " ", plain.upper()).split() if word not in _SUFFIXES}


def _company_names(filing) -> list[str]:
    """The company's name and its former names, as EDGAR records them."""
    cik = str(getattr(filing, "cik", "") or "")
    if cik not in _NAMES:
        names = [str(getattr(filing, "company", "") or "")]
        try:
            from edgar import Company
            names += [item["name"] for item in (Company(int(cik)).data.former_names or [])]
        except Exception:  # noqa: BLE001 - without former names, the current one is compared
            pass
        _NAMES[cik] = [name for name in names if name]
    return _NAMES[cik]


def _cover_text(attachments) -> str | None:
    """A 6-K cover's first words as plain text."""
    cover = next((item for item in attachments if str(item.document_type or "").upper().startswith("6-K")), None)
    if cover is None:
        return None
    content = cover.content
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            content = content.decode("latin-1")  # Petrobras's covers
    text = re.sub(r"<[^>]+>|&nbsp;", " ", html.unescape(str(content or "")) if "&" in str(content or "")[:30_000] else str(content or ""))
    return re.sub(r"\s+", " ", text[:30_000])


def _registrant_words(cover: str | None) -> set[str]:
    """The registrant's names its 6-K cover prints, before "(Exact name of registrant …)" and "(Translation of
    registrant's name …)": FEMSA's "FOMENTO ECONÓMICO MEXICANO, S.A.B. DE C.V." and "Mexican Economic Development, Inc."
    (never the cover's address, whose "Mexico" another issuer's name could share)."""
    words: set[str] = set()
    for match in _REGISTRANT.finditer(cover or ""):
        words |= _named(_BEFORE_NAME.sub("", cover[max(0, match.start() - 150):match.start()]).split(")")[-1])
    return words or _named((cover or "")[:(cover or "").lower().find("indicate by check mark")])  # no markers: its first lines


def another_registrant(ref, attachments) -> str | None:
    """The registrant a 6-K's cover names, when it is neither the company nor a name the company once had."""
    text = _cover_text(attachments)
    if text is None:
        return None
    names = [_words(name) for name in _company_names(ref.filing)]
    found = []
    for match in _REGISTRANT.finditer(text):
        # Each name the cover prints, its exact one and its translation: either may be the company's own (América
        # Móvil's translation "America Mobile" is no EDGAR name, its "AMÉRICA MÓVIL, S.A.B. DE C.V." is).
        printed = _BEFORE_NAME.sub("", text[max(0, match.start() - 150):match.start()]).split(")")[-1].strip(" :.,")
        words = _words(printed)
        if not any(len(word) >= 3 for word in words):
            continue  # no name: Gerdau's cover prints "N/A" for its translation
        if not names or any(words <= name or name <= words or "".join(sorted(words)) == "".join(sorted(name))
                                         or any(SequenceMatcher(None, "".join(_ordered(printed)), "".join(_ordered(other))).ratio() >= 0.85
                                                for other in _company_names(ref.filing))  # "PETR?LEO": one letter lost
                                         for name in names if name):
            return None
        found.append((printed, words))
    # Only a name of the company's own family: a subsidiary shares its brand (Lloyds Bank plc beside Lloyds Banking
    # Group); a name sharing nothing is the company's in another language (Copel's "Energy Company of Paraná").
    return next((printed[:80] for printed, words in found if any(words & name for name in names)), None)


_GENERIC = {"AND", "OF", "DE", "DO", "DA", "DAS", "DOS", "THE", "HOLDINGS", "HOLDING", "GROUP", "INCORPORATED"}


# A listed subsidiary is named for its parent and its market: CEMEX's 6-Ks carry CEMEX Holdings Philippines', CEMEX
# Latam's and CEMEX España's results beside CEMEX's own.
_REGIONS = {"PHILIPPINES", "LATAM", "ESPANA", "SPAIN", "LATIN", "AMERICA", "AMERICAS", "EUROPE", "ASIA", "MEXICO",
            "COLOMBIA", "CHILE", "PERU", "BRASIL", "BRAZIL", "ARGENTINA", "CANADA", "USA", "PANAMA", "EGYPT", "INDIA"}


def _other_companies(readings: list, ref, cover: str | None) -> list[str]:
    """The names of other companies that a release's statement titles name: an acquired company's statements furnished
    with the company's report (Gildan's 6-K of June 2011 carries Gold Toe Moretz's, "GOLD TOE MORETZ HOLDINGS CORP. AND
    SUBSIDIARIES CONSOLIDATED BALANCE SHEETS"), or a listed subsidiary's, its parent's name and a market's. A name is
    the company's own when it shares a word with the company's EDGAR name or a former one, or with the registrant's names
    on the 6-K's cover (FEMSA's titles print "FOMENTO ECONÓMICO MEXICANO", EDGAR "MEXICAN ECONOMIC DEVELOPMENT INC").
    Only names of two words or more are compared, or one in the possessive ("Amaca’s Income Statements"); an annual report, with no cover to give its own names, is never
    checked."""
    if cover is None:
        return []
    own = set().union(*(_named(name) for name in _company_names(ref.filing)), _registrant_words(cover))
    if not own:
        return []
    others = []
    for printed in {reading.titled for reading in readings if reading.titled}:
        words = _named(printed.removesuffix("’s"))
        if (len(words) >= 2 or printed.endswith("’s") and words) and (not words & own and not any(
                SequenceMatcher(None, word, other).ratio() >= 0.85 for word in words for other in own)
                or words & own and (words - own) & _REGIONS):
            others.append(printed)
    return sorted(others)


def _other_issuers(readings: list, ref, cover: str | None) -> list[str]:
    """Issuers whose release opening, in a release, came before the readings, and whose names share no word with the
    company's names or its cover's registrant: Coca-Cola FEMSA in FEMSA's 6-Ks, Brookfield under "BAM" its own."""
    if cover is None:
        return []
    own = set().union(*(_named(name) for name in _company_names(ref.filing)), _registrant_words(cover))
    # Another issuer opens with its legal name ("Coca-Cola FEMSA, S.A.B. de C.V."); a brand ("Volaris*", Controladora
    # Vuela Compañía de Aviación's) or a page footer is the company's own release.
    return sorted(name for name in {reading.release_of for reading in readings if reading.release_of}
                  if own and _named(name) and not _named(name) & own and _LEGAL_FORM.search(name))


_LEGAL_FORM = re.compile(r"\b(?:S\.?\s?A\.?\s?B\.?|S\.?\s?A\.?|Inc\.?|Corp(?:oration)?\.?|Ltd\.?|Limited|plc|N\.?V\.?|AG|"
                         r"Ltda\.?|LLC|Co\.)(?:\s+de\s+C\.?\s?V\.?)?\s*$", re.I)


def _named(name: str) -> set[str]:
    return {word for word in _words(name) - _GENERIC if len(word) >= 3}


def _ordered(name: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", html.unescape(name)).encode("ascii", "ignore").decode()
    return [word for word in re.sub(r"[^A-Z0-9]+", " ", plain.upper()).split() if word not in _SUFFIXES]
