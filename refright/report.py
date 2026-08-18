"""Terminal + JSON reporting."""
from __future__ import annotations

import json
import sys

from .models import EntryResult, Severity

_COLOR = {Severity.ERROR: "\033[31m", Severity.WARNING: "\033[33m",
          Severity.INFO: "\033[36m", Severity.OK: "\033[32m"}
_RESET = "\033[0m"
_LABEL = {Severity.ERROR: "ERROR", Severity.WARNING: "WARN ", Severity.INFO: "INFO "}


def print_summary(results: list[EntryResult], out=sys.stdout) -> None:
    counts = {s: 0 for s in Severity}
    for r in results:
        counts[r.severity] += 1
    print(f"\n{'═' * 72}", file=out)
    print(f"{len(results)} entries | {_COLOR[Severity.OK]}✅ {counts[Severity.OK]}{_RESET} "
          f"{_COLOR[Severity.INFO]}ℹ️  {counts[Severity.INFO]}{_RESET} "
          f"{_COLOR[Severity.WARNING]}⚠️  {counts[Severity.WARNING]}{_RESET} "
          f"{_COLOR[Severity.ERROR]}❌ {counts[Severity.ERROR]}{_RESET}", file=out)


def print_compact(results: list[EntryResult], out=sys.stdout) -> None:
    """Default terminal output: one line per problem entry + summary.
    Full evidence lives in the HTML report; -v prints the long form here."""
    for r in results:
        if r.severity == Severity.OK:
            continue
        c = _COLOR[r.severity]
        top = [f for f in r.findings if f.severity == r.severity]
        codes = ", ".join(dict.fromkeys(f.code for f in top))
        msg = top[0].message if top else (r.findings[0].message if r.findings else "")
        extra = f" (+{len(r.findings) - len(top)} more minor findings)" if len(r.findings) > len(top) else ""
        print(f"{c}{_LABEL[r.severity]}{_RESET} {r.key}  [{codes}] {msg}{extra}", file=out)
    print_summary(results, out)


def print_report(results: list[EntryResult], quiet: bool = False, out=sys.stdout) -> None:
    counts = {s: 0 for s in Severity}
    for r in results:
        counts[r.severity] += 1
        if quiet and r.severity == Severity.OK:
            continue
        if r.severity == Severity.OK:
            print(f"{_COLOR[Severity.OK]}  OK   {_RESET}{r.key}", file=out)
            continue
        c = _COLOR[r.severity]
        print(f"\n{c}{'─' * 72}{_RESET}", file=out)
        print(f"{c}{_LABEL[r.severity]}{_RESET} {r.key}  [{r.entry_type}]  {r.title[:70]}", file=out)
        for f in r.findings:
            fc = _COLOR[f.severity]
            print(f"  {fc}{_LABEL[f.severity]}{_RESET} [{f.code}] {f.message}", file=out)
            dup = (f.fix is not None and len(f.evidence) == 1
                   and f.evidence[0].field == f.fix.field
                   and f.evidence[0].bib_value == f.fix.bib_value
                   and f.evidence[0].ref_value == f.fix.ref_value)
            if not dup:
                for ev in f.evidence:
                    print(f"         {ev.field}: bib = {ev.bib_value!r}  →  record = {ev.ref_value!r}  ({ev.source})",
                          file=out)
                    if ev.url:
                        print(f"           verify: {ev.url}", file=out)
            if f.fix:
                print(f"         🔧 suggested fix: {f.fix.field}: {f.fix.bib_value}  →  {f.fix.ref_value}",
                      file=out)
                if f.fix.url:
                    print(f"           verify: {f.fix.url}", file=out)
            if f.suggestion:
                print(f"         💡 {f.suggestion}", file=out)
    print_summary(results, out)


def to_json(results: list[EntryResult]) -> str:
    def ser(r: EntryResult) -> dict:
        return {
            "key": r.key, "type": r.entry_type, "title": r.title,
            "severity": r.severity.name, "checked_by": r.checked_by,
            "findings": [
                {"severity": f.severity.name, "code": f.code, "message": f.message,
                 "suggestion": f.suggestion,
                 "fix": vars(f.fix) if f.fix else None,
                 "evidence": [vars(ev) for ev in f.evidence]}
                for f in r.findings],
        }
    return json.dumps([ser(r) for r in results], ensure_ascii=False, indent=2)
