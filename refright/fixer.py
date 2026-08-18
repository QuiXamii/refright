"""Surgical, reversible auto-fix for .bib files.

Safety contract (see README §自动修复):
- never rewrites the whole file from parsed fields (formatting/comments survive)
- only edits field values inside the targeted entry block
- default mode is a dry-run diff; writing requires an explicit flag
- in-place writes always create a timestamped backup first
"""
from __future__ import annotations

import difflib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import EntryResult, FieldDiff, Severity

# findings auto-fixed by default (ERROR) vs only with --fix-warnings
DEFAULT_FIXABLE = {"doi-unresolvable", "missing-doi", "pages-mismatch",
                   "volume-mismatch", "year-mismatch", "doi-url-prefix"}
WARNING_FIXABLE = {"issue-mismatch"}

_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)


@dataclass
class Change:
    key: str
    field: str
    old: str
    new: str
    code: str


def collect_fixes(results: list[EntryResult], include_warnings: bool = False) -> dict[str, list[tuple[FieldDiff, str]]]:
    out: dict[str, list[tuple[FieldDiff, str]]] = {}
    for r in results:
        for f in r.findings:
            if f.fix is None:
                continue
            if f.code in DEFAULT_FIXABLE and f.severity <= Severity.ERROR:
                out.setdefault(r.key, []).append((f.fix, f.code))
            elif include_warnings and f.code in WARNING_FIXABLE:
                out.setdefault(r.key, []).append((f.fix, f.code))
    return out


def _bibify_pages(old: str, new: str) -> str:
    """Preserve the bib's dash style: ranges in bibtex conventionally use '--'."""
    if "--" in old and "-" in new and "--" not in new:
        return new.replace("-", "--")
    return new


def _replace_field(block: str, field: str, new_value: str) -> tuple[str, str | None]:
    m = re.search(rf"(?m)^(\s*){re.escape(field)}\s*=\s*([\{{\"])(.*?)([\}}\"])(\s*,?)\s*$", block)
    if not m:
        return block, None
    old_value = m.group(3)
    if field == "pages":
        new_value = _bibify_pages(old_value, new_value)
    start, end = m.span(3)
    return block[:start] + new_value + block[end:], old_value


def _insert_field(block: str, field: str, value: str) -> str:
    lines = block.split("\n")
    for i, ln in enumerate(lines):
        if re.match(r"\s*(author|title)\s*=", ln):
            indent = re.match(r"(\s*)", ln).group(1)
            if not ln.rstrip().endswith(","):
                lines[i] = ln.rstrip() + ","
            lines.insert(i + 1, f"{indent}{field} = {{{value}}},")
            return "\n".join(lines)
    lines.insert(1, f"   {field} = {{{value}}},")
    return "\n".join(lines)


def apply_fixes(text: str, fixes: dict[str, list[tuple[FieldDiff, str]]]
                ) -> tuple[str, list[Change]]:
    """Return (new_text, changes). Pure function — no I/O."""
    changes: list[Change] = []
    spans: list[tuple[int, int, str]] = []
    for m in _ENTRY_RE.finditer(text):
        key = m.group(2).strip()
        if key not in fixes:
            continue
        block = m.group(0)
        new_block = block
        for fx, code in fixes[key]:
            if re.search(rf"(?m)^\s*{re.escape(fx.field)}\s*=", new_block):
                new_block2, old_value = _replace_field(new_block, fx.field, fx.ref_value)
                if old_value is not None and old_value != fx.ref_value:
                    changes.append(Change(key, fx.field, old_value,
                                          _bibify_pages(old_value, fx.ref_value)
                                          if fx.field == "pages" else fx.ref_value, code))
                    new_block = new_block2
            else:  # field missing entirely (e.g. missing-doi) -> insert
                new_block = _insert_field(new_block, fx.field, fx.ref_value)
                changes.append(Change(key, fx.field, "(missing)", fx.ref_value, code))
        if new_block != block:
            spans.append((m.start(), m.end(), new_block))
    new_text = text
    for start, end, repl in sorted(spans, reverse=True):
        new_text = new_text[:start] + repl + new_text[end:]
    return new_text, changes


def unified_diff(old: str, new: str, name: str) -> str:
    return "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        f"a/{name}", f"b/{name}", lineterm="", n=2))


def write_fixed(bib_path: str, new_text: str, out_path: str | None = None) -> str:
    """Write fixed text. In-place writes get a timestamped backup; returns written path."""
    if out_path:
        Path(out_path).write_text(new_text, encoding="utf-8")
        return out_path
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{bib_path}.{ts}.bak"
    shutil.copy2(bib_path, backup)
    Path(bib_path).write_text(new_text, encoding="utf-8")
    return bib_path
