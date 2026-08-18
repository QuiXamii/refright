"""Parse .bbl files (compiled bibliographies from arXiv source tarballs).

Two layouts are supported:

1. REVTeX/APS structured bbl (\\bibfield / \\bibinfo markup) — full field
   extraction: author, title, journal, volume, pages, year, doi, eprint.
2. Plain bbl (free-form \\bibitem text) — best effort: doi, arXiv id, year,
   title (from \\emph/\\textit), volume (from \\textbf).

Missing fields are simply left empty; the engine skips comparisons it has no
data for, and title-based reverse lookup still works when a title was found.
"""
from __future__ import annotations

import re

from .bibparser import BibEntry

_BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_LATEX_CMD = re.compile(r"\\[a-zA-Z@]+\*?")


def looks_like_bbl(text: str) -> bool:
    return "\\bibitem" in text and not re.search(r"@\w+\s*\{", text)


def _braced(text: str, start: int) -> tuple[str, int]:
    """Extract brace-balanced content starting at text[start] == '{'.
    Returns (content, index just past the closing brace)."""
    if start >= len(text) or text[start] != "{":
        return "", start
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], len(text)


def _field(body: str, cmd: str, name: str) -> str:
    """Extract \\cmd {name} {...balanced...} content."""
    m = re.search(r"\\" + cmd + r"\s*\{" + name + r"\}\s*", body)
    if not m:
        return ""
    pos = m.end()
    while pos < len(body) and body[pos] in " \t\n":
        pos += 1
    content, _ = _braced(body, pos)
    return content


def _clean_latex(s: str) -> str:
    s = re.sub(r"\\([^a-zA-Z@])", r"\1", s)   # control symbols: '\ ' '\,' '\;' …
    s = _LATEX_CMD.sub(" ", s)
    s = s.replace("{", " ").replace("}", " ")
    s = s.replace("~", " ").replace("\\&", "&").replace(r"\%", "%")
    return re.sub(r"\s+", " ", s).strip().strip(",").strip()


def _parse_authors(body: str) -> str:
    """REVTeX: \\bibinfo {author} {\\bibfnamefont {Y.}~\\bibnamefont {LeCun}} …
    Returns 'LeCun, Y. and Bengio, Y. and …' (engine's first_surname format)."""
    names = []
    for m in re.finditer(r"\\bibinfo\s*\{author\}\s*", body):
        pos = m.end()
        while pos < len(body) and body[pos] in " \t\n":
            pos += 1
        block, _ = _braced(body, pos)
        fam = re.search(r"\\bibnamefont\s*\{([^}]*)\}", block)
        giv = re.search(r"\\bibfnamefont\s*\{([^}]*)\}", block)
        if fam:
            name = fam.group(1)
            if giv:
                name += ", " + giv.group(1)
            names.append(_clean_latex(name))
    if names:
        return " and ".join(names)
    # plain bbl: text before the first \emph/\textit is usually the author list
    pre = re.split(r"\\emph|\\textit", body, maxsplit=1)[0]
    pre = _clean_latex(pre)
    return pre if pre and len(pre) < 300 else ""


def _extract_doi(body: str) -> str:
    m = re.search(r"\\doibase\s*([^\s}]+)", body)                      # REVTeX \href{\doibase 10.x/..}
    if m:
        return m.group(1).rstrip(".,;")
    m = re.search(r"https?://(?:dx\.)?doi\.org/([^\s},]+)", body)
    if m:
        return m.group(1).rstrip(".,;")
    m = re.search(r"\\doi\s*\{([^}]*)\}", body)
    if m:
        return m.group(1).strip()
    return ""


def _extract_arxiv(body: str) -> str:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", body, re.I)
    if not m:
        m = re.search(r"arXiv:(\d{4}\.\d{4,5})", body, re.I)
    if not m:
        m = re.search(r"arXiv:([a-z\-]+/\d{7})", body, re.I)          # old style quant-ph/9802040
    return m.group(1) if m else ""


def _bare_title(body: str) -> str:
    """REVTeX styles sometimes omit \\bibfield{title} but leave the title as
    bare text right after the author block:
    `…}\\bibfield {author}{…}Riemannian walk for incremental learning\\ (\\bibinfo{year}…`.
    Harvest that free text; return '' when there is none (e.g. straight into
    `in \\href{…}{booktitle}`)."""
    am = re.search(r"\\bibfield\s*\{author\}\s*", body)
    if not am:
        return ""
    pos = am.end()
    while pos < len(body) and body[pos] in " \t\n":
        pos += 1
    _, after = _braced(body, pos)
    free = re.split(r"\\[a-zA-Z@]", body[after:], maxsplit=1)[0]
    title = _clean_latex(free)
    if len(title) < 8 or title.lower() in ("in",):
        return ""
    return title


def _plain_title(body: str) -> str:
    m = re.search(r"\\(?:emph|textit)\s*", body)
    if m:
        pos = m.end()
        while pos < len(body) and body[pos] in " \t\n":
            pos += 1
        content, _ = _braced(body, pos)
        return _clean_latex(content)
    return ""


def parse_bbl(path: str) -> list[BibEntry]:
    text = open(path, encoding="utf-8").read()
    matches = list(_BIBITEM_RE.finditer(text))
    end_m = re.search(r"\\end\{thebibliography\}", text)
    end_all = end_m.start() if end_m else len(text)

    entries: list[BibEntry] = []
    for i, m in enumerate(matches):
        key = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else end_all
        body = text[m.end():end]

        fields: dict[str, str] = {}
        structured = "\\bibfield" in body or "\\bibinfo" in body

        if structured:
            title = _clean_latex(_field(body, "bibfield", "title"))
            if not title:
                title = _bare_title(body)
            journal = _clean_latex(_field(body, "bibinfo", "journal"))
            booktitle = _clean_latex(_field(body, "bibinfo", "booktitle"))
            if journal:
                fields["journal"] = journal
            elif booktitle:
                fields["booktitle"] = booktitle
            for src, dst in (("volume", "volume"), ("pages", "pages"),
                             ("year", "year"), ("number", "issue")):
                v = _clean_latex(_field(body, "bibinfo", src))
                if v:
                    fields[dst] = v
        else:
            title = _plain_title(body)
            ym = re.search(r"\((\d{4})\w*\)", body)
            if ym:
                fields["year"] = ym.group(1)
            vm = re.search(r"\\textbf\s*\{([^}]*)\}", body)
            if vm:
                fields["volume"] = vm.group(1).strip()

        if title:
            fields["title"] = title
        authors = _parse_authors(body)
        if authors:
            fields["author"] = authors
        doi = _extract_doi(body)
        if doi:
            fields["doi"] = doi
        aid = _extract_arxiv(body)
        if aid:
            fields["eprint"] = aid

        # note: 'pages' in REVTeX bbl follows "%Control: page (0) single" —
        # often just the first page; the engine's first-page rule accepts it.
        etype = "article" if fields.get("journal") else (
            "inproceedings" if fields.get("booktitle") else "misc")
        entries.append(BibEntry(key=key, entry_type=etype, fields=fields))
    return entries
