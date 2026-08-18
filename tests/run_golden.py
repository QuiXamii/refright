#!/usr/bin/env python3
"""Golden-set acceptance test for refright.

ref_fixed.bib  : synthetic set of famous public papers, all fields correct
                 -> must yield ZERO errors and ZERO warnings.
ref_broken.bib : same entries with planted errors
                 -> must yield EXACTLY the planted errors (and no other ERROR).
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
TOOL = HERE.parent

BROKEN_ERRORS = {
    "LeCun2015": "doi-unresolvable",
    "Shor1994": "pages-mismatch",
    "Rumelhart1986": "pages-mismatch",
    "Vaswani2017": "title-mismatch",
}


def run(bib: Path) -> list[dict]:
    out = HERE / ".golden_out.json"
    p = subprocess.run(
        [sys.executable, "-m", "refright", str(bib), "--json", str(out), "--no-html", "-q"],
        cwd=TOOL, capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    ok = True

    fixed = run(HERE / "golden" / "ref_fixed.bib")
    errs = [r for r in fixed if r["severity"] == "ERROR"]
    warns = [r for r in fixed if r["severity"] == "WARNING"]
    print(f"\n[fixed] errors={len(errs)} warnings={len(warns)} (expect 0/0)")
    if errs or warns:
        ok = False
        for r in errs + warns:
            print("  UNEXPECTED:", r["key"], [f["code"] for f in r["findings"]])

    broken = run(HERE / "golden" / "ref_broken.bib")
    found = {}
    for r in broken:
        for f in r["findings"]:
            if f["severity"] == "ERROR":
                found.setdefault(r["key"], []).append(f["code"])
    print(f"\n[broken] error entries: {sorted(found)} (expect {len(BROKEN_ERRORS)})")
    for key, code in BROKEN_ERRORS.items():
        if key not in found:
            ok = False
            print(f"  MISSING: {key} expected error code {code}")
        elif code not in found[key]:
            ok = False
            print(f"  WRONG CODE: {key} expected {code}, got {found[key]}")
    extra = set(found) - set(BROKEN_ERRORS)
    if extra:
        ok = False
        print(f"  EXTRA ERRORS: {extra}")

    # M2: HTML report must carry evidence + verification links for the 5 errors
    html_out = HERE / "golden" / "ref_broken_report.html"
    subprocess.run([sys.executable, "-m", "refright", str(HERE / "golden" / "ref_broken.bib"),
                    "--html", str(html_out), "-q"], cwd=TOOL, capture_output=True, text=True)
    doc = html_out.read_text(encoding="utf-8")
    print(f"\n[html] {html_out.name}: {len(doc)} bytes")
    for marker in ["LeCun2015", "10.1038/nature14539",
                   "124-134", "533-536", "Vaswani2017",
                   "核实 ↗", "核查来源"]:
        if marker not in doc:
            ok = False
            print(f"  HTML MISSING: {marker}")

    print("\n=== GOLDEN TEST:", "PASS ✅" if ok else "FAIL ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
