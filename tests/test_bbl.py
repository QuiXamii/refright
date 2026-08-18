#!/usr/bin/env python3
""".bbl support acceptance test.

Fixture: tests/golden/sample.bbl — a synthetic REVTeX/APS-style bbl (7 entries,
all famous public papers; \\bibfield/\\bibinfo markup + one plain-format entry).
Planted issues:
  - gpt4report      arXiv id conflict (printed id vs hyperlink differ)
                    + arxiv-id-mismatch
  - shor1994        first page off by one (125--134 vs record 124-134)
                    -> pages-mismatch
  - zenke2017       title omitted by the bbl style -> not-found-in-databases
  - gao2018quantum  cites article number as in the DOI suffix (aat9004 vs
                    Crossref eaat9004): must stay CLEAN (regression lock)

1. parser: 7 entries; structured fields extracted correctly (lecun2015deep);
   bare-title fallback works (chaudhry2018 has no \\bibfield{title})
2. integration: full run must flag exactly the planted ERRORs above
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TOOL = HERE.parent
BBL = HERE / "golden" / "sample.bbl"

# Infrastructure-level findings (a source was unreachable, not a bib problem).
# Tolerated so a transient DBLP/arXiv/Crossref outage doesn't fail CI.
INFRA_CODES = {"dblp-check-failed", "arxiv-check-failed", "doi-check-failed"}


def main() -> int:
    ok = True
    sys.path.insert(0, str(TOOL))
    from refright.bblparser import parse_bbl

    entries = parse_bbl(str(BBL))
    print(f"[bbl] parsed {len(entries)} entries (expect 7)")
    if len(entries) != 7:
        ok = False

    e = next(x for x in entries if x.key == "lecun2015deep")
    expect = {"title": "Deep learning", "journal": "Nature", "volume": "521",
              "pages": "436", "year": "2015", "doi": "10.1038/nature14539"}
    for k, v in expect.items():
        if e.fields.get(k) != v:
            print(f"[bbl] lecun2015deep.{k} = {e.fields.get(k)!r}, expect {v!r}")
            ok = False
    if not e.fields.get("author", "").startswith("LeCun"):
        print(f"[bbl] lecun2015deep.author = {e.fields.get('author')!r}")
        ok = False

    ch = next(x for x in entries if x.key == "chaudhry2018")
    if not ch.fields.get("title", "").startswith("Riemannian walk for incremental learning"):
        print(f"[bbl] chaudhry2018 bare title failed: {ch.fields.get('title')!r}")
        ok = False

    with tempfile.TemporaryDirectory() as td:
        out_json = Path(td) / "out.json"
        p = subprocess.run(
            [sys.executable, "-m", "refright", str(BBL), "--json", str(out_json),
             "--no-html", "-q"],
            cwd=TOOL, capture_output=True, text=True)
        if p.returncode != 1:
            sys.stderr.write(p.stderr[-2000:])
            print(f"[bbl] exit code {p.returncode}, expect 1 (planted errors)")
            ok = False
        else:
            data = json.loads(out_json.read_text(encoding="utf-8"))
            errors = {(r["key"], f["code"]) for r in data for f in r["findings"]
                      if f["severity"] == "ERROR"}
            expect_errors = {("gpt4report", "arxiv-id-conflict"),
                             ("gpt4report", "arxiv-id-mismatch"),
                             ("shor1994", "pages-mismatch")}
            print(f"[bbl] ERRORs: {sorted(errors)}")
            if errors != expect_errors:
                print(f"[bbl] expect exactly {sorted(expect_errors)}")
                ok = False
            warns = {(r["key"], f["code"]) for r in data for f in r["findings"]
                     if f["severity"] == "WARNING"
                     and f["code"] not in INFRA_CODES}
            if warns != {("zenke2017", "not-found-in-databases")}:
                print(f"[bbl] unexpected WARNINGs: {sorted(warns)}")
                ok = False

    print("\n=== BBL TEST:", "PASS ✅" if ok else "FAIL ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
