"""Command-line interface.

python -m refright ref.bib [--json out.json] [--html out.html] [-q] [--workers N]
                           [--fix [--write | --fix-out PATH] [--fix-warnings]]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from .bibparser import parse_bib
from .cache import Cache
from .engine import Engine
from .fixer import apply_fixes, collect_fixes, unified_diff, write_fixed
from .models import EntryResult, Severity
from .report import print_compact, print_report, to_json


def _fmt_secs(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m}:{sec:02d}"


def make_progress():
    """Progress callback: animated single line on a TTY, periodic lines otherwise."""
    is_tty = sys.stderr.isatty()
    width = max(60, shutil.get_terminal_size((100, 20)).columns - 1)
    start = time.time()
    tallies = {Severity.ERROR: 0, Severity.WARNING: 0, Severity.INFO: 0}
    state = {"next_mark": 0}

    def cb(done: int, total: int, r) -> None:
        if r.severity in tallies:
            tallies[r.severity] += 1
        elapsed = time.time() - start
        eta = elapsed / done * (total - done) if done else 0
        pct = done * 100 // total
        if is_tty:
            filled = int(20 * done / total)
            bar = "█" * filled + "░" * (20 - filled)
            line = (f"[{done}/{total}] {bar} {pct}% | elapsed {_fmt_secs(elapsed)} "
                    f"ETA {_fmt_secs(eta)} | ❌{tallies[Severity.ERROR]} "
                    f"⚠️{tallies[Severity.WARNING]} ℹ️{tallies[Severity.INFO]} | {r.key}")
            print("\r" + line.ljust(width)[:width], end="", file=sys.stderr, flush=True)
            if done == total:
                print(file=sys.stderr)
        else:
            mark = state["next_mark"]
            if done == total or done >= mark:
                state["next_mark"] = mark + max(1, total // 10)
                print(f"[{done}/{total}] {pct}% | elapsed {_fmt_secs(elapsed)} | "
                      f"❌{tallies[Severity.ERROR]} ⚠️{tallies[Severity.WARNING]} "
                      f"ℹ️{tallies[Severity.INFO]}", file=sys.stderr, flush=True)

    return cb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="refright",
        description="Verify every reference in a .bib file against Crossref / OpenAlex / arXiv / DBLP.")
    ap.add_argument("bib", help="path to the .bib file")
    ap.add_argument("--json", metavar="PATH", help="write machine-readable JSON report")
    ap.add_argument("--html", metavar="PATH", help="HTML report path "
                    "(default: <input>_refright_report.html next to the input file)")
    ap.add_argument("--no-html", action="store_true", help="disable the automatic HTML report")
    ap.add_argument("--no-cache", action="store_true", help="bypass the local response cache")
    ap.add_argument("--workers", type=int, default=6, metavar="N",
                    help="concurrent verification workers (default 6; 1 = serial)")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="with -v: hide entries that pass all checks")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print the full per-entry terminal report (default: compact summary)")
    ap.add_argument("--tex", action="append", metavar="PATH",
                    help="only check entries actually \\cite'd in these .tex files/dirs "
                         "(repeatable); cited-but-missing keys become ERRORs")
    ap.add_argument("--fix", action="store_true",
                    help="preview auto-fixes (dry run; writes nothing)")
    ap.add_argument("--write", action="store_true",
                    help="with --fix: apply fixes IN PLACE (timestamped backup is created first)")
    ap.add_argument("--fix-out", metavar="PATH",
                    help="with --fix: write the fixed bib to PATH instead of modifying the original")
    ap.add_argument("--fix-warnings", action="store_true",
                    help="with --fix: also apply WARNING-level fixes (e.g. issue-mismatch)")
    args = ap.parse_args(argv)

    if (args.write or args.fix_out) and not args.fix:
        ap.error("--write/--fix-out require --fix")

    head = Path(args.bib).read_text(encoding="utf-8", errors="replace")[:200_000]
    from .bblparser import looks_like_bbl, parse_bbl
    is_bbl = args.bib.lower().endswith(".bbl") or looks_like_bbl(head)
    if is_bbl and args.fix:
        ap.error("--fix only supports .bib sources; .bbl is a build artifact — fix the .bib that generated it and recompile")

    entries = parse_bbl(args.bib) if is_bbl else parse_bib(args.bib)
    all_entries = entries  # the HTML report needs the full entry list (incl. uncited) to show bib fields

    uncited: list = []
    missing_keys: list[str] = []
    if args.tex:
        from .texscan import cited_keys
        cited, cite_all = cited_keys(args.tex)
        if cite_all:
            print(f"refright: detected \\nocite{{*}}, checking all {len(entries)} entries…", file=sys.stderr)
        else:
            cited_set = set(cited)
            bib_keys = {e.key for e in entries}
            missing_keys = [k for k in cited if k not in bib_keys]
            uncited = [e for e in entries if e.key not in cited_set]
            entries = [e for e in entries if e.key in cited_set]
            print(f"refright: --tex: {len(cited)} keys actually cited; bib has {len(entries) + len(uncited)} entries, "
                  f"{len(uncited)} uncited skipped, {len(missing_keys)} cited keys missing from the bib",
                  file=sys.stderr)

    print(f"refright: checking {len(entries)} references ({'bbl' if is_bbl else 'bib'} format, "
          f"{args.workers} workers)…", file=sys.stderr)

    cache = Cache(enabled=not args.no_cache)
    t0 = time.time()
    try:
        results = Engine(cache).check_all(entries, workers=args.workers,
                                          progress=make_progress())
    finally:
        cache.close()
    print(f"done in {_fmt_secs(time.time() - t0)}", file=sys.stderr)

    if args.tex and missing_keys:
        from .models import FieldDiff, Finding
        for k in missing_keys:
            r = EntryResult(key=k, entry_type="(missing)", title="")
            r.findings.append(Finding(
                Severity.ERROR, "cited-not-in-bib",
                "cited in the .tex file(s), but no such entry exists in the bib (compiling would produce undefined citations)",
                evidence=[FieldDiff("key", k, "missing from bib", "tex", "")]))
            results.insert(0, r)
    if uncited:
        from .models import FieldDiff, Finding
        for e in uncited:
            r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
            r.findings.append(Finding(
                Severity.INFO, "not-cited",
                "not cited by the --tex document(s); skipped — consider removing it from the bib"))
            results.append(r)

    if args.verbose:
        print_report(results, quiet=args.quiet)
    else:
        print_compact(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(to_json(results))
        print(f"JSON report written to {args.json}", file=sys.stderr)

    html_path = args.html
    if html_path is None and not args.no_html:
        src = Path(args.bib)
        html_path = str(src.with_name(src.stem + "_refright_report.html"))
    if html_path:
        from .report_html import render_html
        tex_note = ""
        if args.tex:
            tex_note = (f"--tex {', '.join(args.tex)}: {len(entries)} cited entries checked, "
                        f"{len(uncited)} uncited skipped, "
                        f"{len(missing_keys)} cited keys missing from the bib")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(render_html(results, all_entries, args.bib, tex_note=tex_note))
        print(f"HTML report written to {html_path} (--no-html to disable, -v for the full terminal report)", file=sys.stderr)

    if args.fix:
        fixes = collect_fixes(results, include_warnings=args.fix_warnings)
        if not fixes:
            print("\n--fix: nothing auto-fixable (WARNING-level fixes need --fix-warnings)", file=sys.stderr)
        else:
            old_text = Path(args.bib).read_text(encoding="utf-8")
            new_text, changes = apply_fixes(old_text, fixes)
            n_entries = len(fixes)
            print(f"\n--fix: {len(changes)} auto-fixable fields across {n_entries} entries:", file=sys.stderr)
            for c in changes:
                print(f"  [{c.code}] {c.key}: {c.field}: {c.old} → {c.new}", file=sys.stderr)
            if args.write or args.fix_out:
                written = write_fixed(args.bib, new_text, args.fix_out)
                if args.fix_out:
                    print(f"fixed bib written to {written} (original untouched)", file=sys.stderr)
                else:
                    print(f"fixed {written} in place (original backed up as a timestamped .bak)", file=sys.stderr)
                print("Re-run refright to double-check the result.", file=sys.stderr)
            else:
                print("\n--- diff preview (dry run; nothing written — use --write to apply in place or --fix-out PATH to save elsewhere) ---",
                      file=sys.stderr)
                print(unified_diff(old_text, new_text, Path(args.bib).name))

    return 1 if any(r.severity == Severity.ERROR for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
