"""Scan .tex sources for citation keys actually used via \\cite-like commands.

Handles: \\cite / \\citep / \\citet / \\nocite / \\autocite / \\parencite …
(any command containing "cite"), optional arguments, comma-separated keys,
% comments, and \\nocite{*} (means: everything is cited).
"""
from __future__ import annotations

import re
from pathlib import Path

# \cite[see][p. 5]{key1, key2}  /  \nocite{key}
_CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\s*(?:\[[^\]]*\])*\s*\{([^}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")


def _tex_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.rglob("*.tex")))
        elif pp.is_file():
            files.append(pp)
        else:
            raise FileNotFoundError(f"--tex path does not exist: {p}")
    return files


def cited_keys(paths: list[str]) -> tuple[list[str], bool]:
    """Return (keys in first-appearance order, cite_all).

    cite_all is True when a \\nocite{*} was seen — caller should not filter.
    """
    keys: list[str] = []
    seen: set[str] = set()
    cite_all = False
    for f in _tex_files(paths):
        text = _COMMENT_RE.sub("", f.read_text(encoding="utf-8"))
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
                if not k:
                    continue
                if k == "*":
                    cite_all = True
                    continue
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
    return keys, cite_all
