#!/usr/bin/env python3
"""--tex filter acceptance test.

Fixture tests/golden/citations.tex cites LeCun2015, Shor1994, Vaswani2017
(all present in ref_fixed.bib) plus GhostKey2099 (missing) and
CommentedOutKey (inside a % comment — must be ignored).

Expectations:
1. only the 3 cited entries are actually checked (plus 1 synthetic ERROR
   entry for the missing key + INFO not-cited entries for the rest)
2. GhostKey2099 -> ERROR cited-not-in-bib, exit code 1
3. CommentedOutKey never appears anywhere
4. all checked real entries verify clean (ref_fixed.bib is the known-good bib)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TOOL = HERE.parent
FIXED = HERE / "golden" / "ref_fixed.bib"
TEX = HERE / "golden" / "citations.tex"


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as td:
        out_json = Path(td) / "out.json"
        p = subprocess.run(
            [sys.executable, "-m", "refright", str(FIXED), "--tex", str(TEX),
             "--json", str(out_json), "--no-html", "-q"],
            cwd=TOOL, capture_output=True, text=True)
        if p.returncode not in (0, 1):
            sys.stderr.write(p.stderr)
            raise RuntimeError("refright crashed")
        data = json.loads(out_json.read_text(encoding="utf-8"))
        all_keys = {r["key"] for r in data}
        total_bib = sum(1 for line in FIXED.read_text(encoding="utf-8").splitlines()
                        if line.startswith("@"))

        # 1. GhostKey2099 must be the only ERROR (cited-not-in-bib)
        errors = [(r["key"], f["code"]) for r in data for f in r["findings"]
                  if f["severity"] == "ERROR"]
        print(f"[tex] ERRORs: {errors} (expect [('GhostKey2099', 'cited-not-in-bib')])")
        if errors != [("GhostKey2099", "cited-not-in-bib")]:
            ok = False
        if p.returncode != 1:
            print("[tex] exit code != 1 despite cited-not-in-bib")
            ok = False

        # 2. comment must be ignored
        if "CommentedOutKey" in all_keys:
            print("[tex] CommentedOutKey leaked from a % comment")
            ok = False

        # 3. coverage: 3 cited + (total-3) not-cited + 1 missing = total+1
        not_cited = [r["key"] for r in data
                     if any(f["code"] == "not-cited" for f in r["findings"])]
        print(f"[tex] cited={total_bib - len(not_cited)} not-cited={len(not_cited)} "
              f"(bib total={total_bib})")
        if len(not_cited) != total_bib - 3 or len(data) != total_bib + 1:
            ok = False

        # 4. cited real entries must be clean (known-good bib)
        bad = [(r["key"], f["code"]) for r in data
               if r["key"] in ("LeCun2015", "Shor1994", "Vaswani2017")
               for f in r["findings"] if f["severity"] in ("ERROR", "WARNING")]
        if bad:
            print(f"[tex] unexpected findings on cited entries: {bad}")
            ok = False

    print("\n=== TEX TEST:", "PASS ✅" if ok else "FAIL ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
