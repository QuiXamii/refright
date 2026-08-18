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
            line = (f"[{done}/{total}] {bar} {pct}% | 已用 {_fmt_secs(elapsed)} "
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
        ap.error("--write/--fix-out 必须与 --fix 一起使用")

    head = Path(args.bib).read_text(encoding="utf-8", errors="replace")[:200_000]
    from .bblparser import looks_like_bbl, parse_bbl
    is_bbl = args.bib.lower().endswith(".bbl") or looks_like_bbl(head)
    if is_bbl and args.fix:
        ap.error("--fix 仅支持 .bib 源文件；.bbl 是编译产物，请修正生成它的 .bib 后重新编译")

    entries = parse_bbl(args.bib) if is_bbl else parse_bib(args.bib)
    all_entries = entries  # HTML 报告需要完整条目表（含未引用条目）来展示 bib 字段

    uncited: list = []
    missing_keys: list[str] = []
    if args.tex:
        from .texscan import cited_keys
        cited, cite_all = cited_keys(args.tex)
        if cite_all:
            print(f"refright: 检测到 \\nocite{{*}}，按全部 {len(entries)} 条核查…", file=sys.stderr)
        else:
            cited_set = set(cited)
            bib_keys = {e.key for e in entries}
            missing_keys = [k for k in cited if k not in bib_keys]
            uncited = [e for e in entries if e.key not in cited_set]
            entries = [e for e in entries if e.key in cited_set]
            print(f"refright: --tex 实际引用 {len(cited)} 条；bib 共 {len(entries) + len(uncited)} 条，"
                  f"{len(uncited)} 条未引用跳过核查，{len(missing_keys)} 个引用 key 在 bib 中不存在",
                  file=sys.stderr)

    print(f"refright: 核查 {len(entries)} 条文献（{'bbl' if is_bbl else 'bib'} 格式，"
          f"{args.workers} 路并发）…", file=sys.stderr)

    cache = Cache(enabled=not args.no_cache)
    t0 = time.time()
    try:
        results = Engine(cache).check_all(entries, workers=args.workers,
                                          progress=make_progress())
    finally:
        cache.close()
    print(f"核查完成，用时 {_fmt_secs(time.time() - t0)}", file=sys.stderr)

    if args.tex and missing_keys:
        from .models import FieldDiff, Finding
        for k in missing_keys:
            r = EntryResult(key=k, entry_type="(missing)", title="")
            r.findings.append(Finding(
                Severity.ERROR, "cited-not-in-bib",
                "正文引用了该 key，但 bib 中不存在此条目（编译会产生未定义引用）",
                evidence=[FieldDiff("key", k, "bib 中不存在", "tex", "")]))
            results.insert(0, r)
    if uncited:
        from .models import FieldDiff, Finding
        for e in uncited:
            r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
            r.findings.append(Finding(
                Severity.INFO, "not-cited",
                "条目未被 --tex 指定的文档引用，未核查（可考虑从 bib 中清理）"))
            results.append(r)

    if args.verbose:
        print_report(results, quiet=args.quiet)
    else:
        print_compact(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(to_json(results))
        print(f"JSON 报告已写入 {args.json}", file=sys.stderr)

    html_path = args.html
    if html_path is None and not args.no_html:
        src = Path(args.bib)
        html_path = str(src.with_name(src.stem + "_refright_report.html"))
    if html_path:
        from .report_html import render_html
        tex_note = ""
        if args.tex:
            tex_note = (f"--tex {', '.join(args.tex)}：实际引用 {len(entries)} 条已核查，"
                        f"{len(uncited)} 条未引用未核查，"
                        f"{len(missing_keys)} 个引用 key 在 bib 中缺失")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(render_html(results, all_entries, args.bib, tex_note=tex_note))
        print(f"HTML 报告已写入 {html_path}（--no-html 关闭，-v 看完整终端报告）", file=sys.stderr)

    if args.fix:
        fixes = collect_fixes(results, include_warnings=args.fix_warnings)
        if not fixes:
            print("\n--fix: 没有可自动修复的问题（WARNING 级需加 --fix-warnings）", file=sys.stderr)
        else:
            old_text = Path(args.bib).read_text(encoding="utf-8")
            new_text, changes = apply_fixes(old_text, fixes)
            n_entries = len(fixes)
            print(f"\n--fix: {len(changes)} 处可自动修复，涉及 {n_entries} 个条目：", file=sys.stderr)
            for c in changes:
                print(f"  [{c.code}] {c.key}: {c.field}: {c.old} → {c.new}", file=sys.stderr)
            if args.write or args.fix_out:
                written = write_fixed(args.bib, new_text, args.fix_out)
                if args.fix_out:
                    print(f"修复后的 bib 已写入 {written}（原文件未动）", file=sys.stderr)
                else:
                    print(f"已原地修复 {written}（原文件已备份为时间戳 .bak）", file=sys.stderr)
                print("建议重新运行 refright 复查修复结果。", file=sys.stderr)
            else:
                print("\n--- diff 预览（dry-run，未写入任何文件；加 --write 原地修复或 --fix-out PATH 另存）---",
                      file=sys.stderr)
                print(unified_diff(old_text, new_text, Path(args.bib).name))

    return 1 if any(r.severity == Severity.ERROR for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
