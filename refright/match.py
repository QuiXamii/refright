"""Text normalization and fuzzy matching for titles, journals, authors."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
_NONALNUM = re.compile(r"[^a-z0-9 ]+")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_title(s: str) -> str:
    s = _fold(s.lower())
    s = _LATEX_CMD.sub(" ", s)
    s = s.replace("{", " ").replace("}", " ").replace("$", " ")
    s = _NONALNUM.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_sim(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    return 0.6 * ratio + 0.4 * jaccard


# --- journal name matching ---
# Both abbreviated and full tokens are canonicalized to a common stem,
# so "Commun. Math. Phys." and "Communications in Mathematical Physics" coincide.

_STEM = {
    "phys": "phys", "physical": "phys", "physics": "phys",
    "rev": "rev", "review": "rev", "reviews": "rev",
    "lett": "lett", "letters": "lett",
    "res": "res", "research": "res",
    "commun": "commun", "communications": "commun", "communication": "commun",
    "math": "math", "mathematical": "math", "mathematics": "math",
    "proc": "proc", "proceedings": "proc",
    "natl": "natl", "national": "natl",
    "acad": "acad", "academy": "acad",
    "sci": "sci", "science": "sci", "sciences": "sci",
    "ann": "ann", "annals": "ann",
    "trans": "trans", "transactions": "trans",
    "technol": "technol", "technology": "technol",
    "quant": "quant", "quantum": "quant",
    "inf": "inf", "information": "inf",
    "appl": "appl", "applied": "appl",
    "int": "int", "international": "int",
    "j": "j", "journal": "j",
    "comput": "comput", "computational": "comput", "computing": "comput",
    "syst": "syst", "systems": "syst",
    "mod": "mod", "modern": "mod",
    "rep": "rep", "reports": "rep",
    "adv": "adv", "advances": "adv",
    "bull": "bull", "bulletin": "bull",
    "inst": "inst", "institute": "inst",
    "conf": "conf", "conference": "conf",
    "symp": "symp", "symposium": "symp",
    "mach": "mach", "machine": "mach",
    "intell": "intell", "intelligence": "intell",
    "anal": "anal", "analysis": "anal",
    "stat": "stat", "statistics": "stat",
    "theor": "theor", "theoretical": "theor",
    "exp": "exp", "experimental": "exp",
    "mat": "mat", "materials": "mat",
    "soc": "soc", "society": "soc",
    "ser": "ser", "series": "ser",
    "lect": "lect", "lecture": "lect",
    "eng": "eng", "engineering": "eng",
    "artif": "artif", "artificial": "artif",
}
_STOP = {"in", "of", "the", "and", "on", "for", "an", "a", "der", "de", "und"}


def norm_journal(s: str) -> str:
    s = _fold(s.lower())
    s = _LATEX_CMD.sub(" ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    toks = [t for t in s.split() if t not in _STOP]
    toks = [_STEM.get(t, t) for t in toks]
    return " ".join(toks)


def journal_match(bib_j: str, ref_j: str) -> bool:
    if not bib_j or not ref_j:
        return True
    na, nb = norm_journal(bib_j), norm_journal(ref_j)
    if na == nb:
        return True
    # tolerate trailing series info like "... conference track proceedings"
    return na in nb or nb in na


def first_surname(author_field: str) -> str:
    first = re.split(r"\s+and\s+", author_field)[0].strip()
    if "," in first:
        last = first.split(",")[0]
    else:
        toks = first.split()
        last = toks[-1] if toks else ""
    return norm_title(last).replace(" ", "")


def norm_pages(p: str) -> str:
    s = re.sub(r"[-\u2013]{1,2}", "-", (p or "").replace(" ", ""))
    # leading zeros carry no information: '061' and '61' are the same page
    return "-".join(str(int(t)) if t.isdigit() else t for t in s.split("-"))


def first_page(p: str) -> str:
    return norm_pages(p).split("-")[0]
