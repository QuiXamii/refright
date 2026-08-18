"""Robust .bib parsing (stdlib only).

Tolerates: fields in any order, last field without trailing comma,
brace or quote delimiters, multi-line values, comments between entries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class BibEntry:
    key: str
    entry_type: str          # article / inproceedings / misc / ...
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.fields.get("title", "")


_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)
_FIELD_RE = re.compile(r"(\w+)\s*=\s*[\{\"](.*?)[\}\"]\s*,?\s*(?:\n|$)", re.S)


def parse_bib(path: str) -> list[BibEntry]:
    text = open(path, encoding="utf-8").read()
    entries: list[BibEntry] = []
    for m in _ENTRY_RE.finditer(text):
        etype, key, body = m.group(1).lower(), m.group(2).strip(), m.group(3)
        if etype in ("comment", "preamble", "string"):
            continue
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(body):
            val = re.sub(r"\s+", " ", fm.group(2)).strip()
            fields[fm.group(1).lower()] = val
        entries.append(BibEntry(key=key, entry_type=etype, fields=fields))
    return entries


def extract_arxiv_ids(entry: BibEntry) -> list[str]:
    """Pull every arXiv id from eprint / url / howpublished / journal / note,
    de-duplicated, in field-priority order (eprint first).

    The journal field is included because many auto-generated bibs cite
    preprints as ``journal = {arXiv:1901.06523}``; bare ``eprint = {1901.06523}``
    values are matched too. More than one id means the entry is internally
    inconsistent (engine reports arxiv-id-conflict).
    """
    out: list[str] = []
    for f in ("eprint", "url", "howpublished", "journal", "note"):
        v = entry.fields.get(f, "").strip()
        if not v:
            continue
        m = None
        if f == "eprint":
            m = (re.match(r"^(\d{4}\.\d{4,5})(v\d+)?$", v)
                 or re.match(r"^([a-z\-]+/\d{7})(v\d+)?$", v))
        if m is None:
            m = re.search(r"arxiv[^0-9]*(\d{4}\.\d{4,5})", v, re.I)
        if m is None:
            m = re.search(r"arxiv:([a-z\-]+/\d{7})", v, re.I)  # old style e.g. quant-ph/9802040
        if m:
            aid = m.group(1)
            if aid not in out:
                out.append(aid)
    return out


def extract_arxiv_id(entry: BibEntry) -> str | None:
    ids = extract_arxiv_ids(entry)
    return ids[0] if ids else None
