#!/usr/bin/env python3
"""--fix acceptance test.

1. ref_broken.bib --fix --write        -> all fixable ERRORs repaired; exactly
                                          1 ERROR left (title-mismatch needs a
                                          human) and 1 WARNING (issue-mismatch)
2. ref_broken.bib --fix --write --fix-warnings -> 1 ERROR, 0 WARNING
3. timestamped backup must exist and equal the original bytes
"""
import glob
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TOOL = HERE.parent
BROKEN = HERE / "golden" / "ref_broken.bib"


def run(args: list[str]) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, "-m", "refright", *args],
                       cwd=TOOL, capture_output=True, text=True)
    if p.returncode not in (0, 1):
        sys.stderr.write(p.stderr)
        raise RuntimeError("refright crashed")
    return p


def severity_count(json_path: Path) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out = {"ERROR": 0, "WARNING": 0, "INFO": 0, "OK": 0}
    for r in data:
        out[r["severity"]] += 1
    return out


def one_round(include_warnings: bool, tmpdir: Path) -> tuple[dict, str]:
    work = tmpdir / f"work_{'w' if include_warnings else 'e'}.bib"
    shutil.copy2(BROKEN, work)
    original = work.read_bytes()

    args = [str(work), "--fix", "--write", "--no-html", "-q"]
    if include_warnings:
        args.append("--fix-warnings")
    run(args)

    backups = glob.glob(str(work) + ".*.bak")
    assert len(backups) == 1, f"expected exactly 1 backup, got {backups}"
    assert Path(backups[0]).read_bytes() == original, "backup content differs from original!"

    out_json = tmpdir / "recheck.json"
    run([str(work), "--json", str(out_json), "--no-html", "-q"])
    return severity_count(out_json), backups[0]


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        counts, _ = one_round(False, tmpdir)
        print(f"[fix default] after-fix recheck: {counts} (expect ERROR=1, WARNING=1)")
        if counts["ERROR"] != 1 or counts["WARNING"] != 1:
            ok = False

        counts, _ = one_round(True, tmpdir)
        print(f"[fix+warnings] after-fix recheck: {counts} (expect ERROR=1, WARNING=0)")
        if counts["ERROR"] != 1 or counts["WARNING"] != 0:
            ok = False

    print("\n=== FIX TEST:", "PASS ✅" if ok else "FAIL ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
