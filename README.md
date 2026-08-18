# refright

Check that your references are real, and that they say what your bibliography claims they say.

refright reads a `.bib` or `.bbl` file and verifies every entry against Crossref, OpenAlex, arXiv, DBLP, and DataCite. It catches DOIs that don't resolve, DOIs that resolve to a different paper entirely, wrong page numbers, arXiv IDs pointing at someone else's preprint, and entries no database has ever heard of. For the mechanical mistakes it can write the fix itself.

[中文文档](README_zh.md)

## Why I wrote it

A manual pass over a 64-entry `ref.bib` turned up two wrong DOIs and three wrong page ranges. Two of those had already survived careful human review. These errors are boring, mechanical, and embarrassing, which makes them exactly the kind a script should catch and a human shouldn't have to.

## Install

```bash
git clone https://github.com/QuiXamii/refright
cd refright
pip install .
```

Pure standard library, Python 3.10+. If you'd rather not install anything, `python -m refright ...` works from the repo root.

## Use it

```bash
refright ref.bib
```

The terminal stays short: one line per problem entry, then a tally. The evidence goes into an HTML report written next to your input file (`ref.bib` → `ref_refright_report.html`). Every finding there shows the bib value next to the database record, a suggested correction when one exists, and a link so you can verify the claim with your own eyes. See [a report with errors](docs/demo_broken.html), [a clean one](docs/demo_clean.html), or [one from a .bbl file](docs/demo_bbl.html).

```bash
refright ref.bib -v                 # full per-entry report in the terminal too
refright ref.bib --html my.html     # pick the report path yourself
refright ref.bib --no-html          # no HTML (pair with --json for CI)
refright ref.bib --json out.json    # machine-readable report
refright ref.bib --workers 8        # concurrency (default 6; 1 = serial)
```

Checks run concurrently with per-source rate limits, so a 367-entry bibliography takes under a minute on a cold cache and seconds on a warm one. A progress bar keeps you company on TTY; in a pipe you get a line every 10%. Any ERROR sets exit code 1, so the tool drops straight into a pre-commit hook or a CI gate.

### Only check what you actually cite

```bash
refright ref.bib --tex main.tex
refright ref.bib --tex main.tex --tex supp.tex   # or pass a directory
```

All `\cite`-family commands count (`\citep`, `\citet`, `\nocite`, `\autocite`, …), `%` comments are ignored, and `\nocite{*}` means everything. A key cited in the text but missing from the bib is an ERROR, since that's an undefined citation waiting to fail your compile. Entries nobody cites are skipped and marked `not-cited`, which makes pruning the bib easy.

### .bbl files

arXiv source tarballs often ship only the compiled `ref.bbl`. Hand it over directly:

```bash
refright ref.bbl
```

REVTeX/APS markup (`\bibfield` / `\bibinfo`) is parsed in full, including the bare-title fallback for styles that omit `\bibfield{title}`. Plain `\bibitem` prose gets best-effort extraction of DOI, arXiv ID, year, `\emph` title, and `\textbf` volume. `--fix` is disabled for .bbl files: it's a build artifact, so fix the `.bib` and recompile.

### Auto-fix

```bash
refright ref.bib --fix                      # dry run: lists changes, shows a diff, writes nothing
refright ref.bib --fix --fix-out fixed.bib  # write the fixed bib elsewhere
refright ref.bib --fix --write              # in place (timestamped .bak backup first)
refright ref.bib --fix --write --fix-warnings   # also fix WARNING level (issue numbers)
```

The fixer edits field lines in place and never rewrites the file, so comments and formatting survive. By default it only touches ERROR-level fields and missing DOIs. Run refright again afterwards to confirm the fixes took.

## What it catches

| code | severity | meaning | auto-fix |
| ---- | -------- | ------- | -------- |
| `doi-unresolvable` | ERROR | DOI 404s; a reverse title lookup suggests the correct one | ✅ |
| `title-mismatch` | ERROR | the DOI resolves to a different paper | ❌ human |
| `pages-mismatch` | ERROR | pages / article number disagree with the publisher or DBLP | ✅ |
| `volume-mismatch` / `year-mismatch` | ERROR | volume / year disagree with the record | ✅ |
| `arxiv-id-not-found` / `-mismatch` | ERROR | arXiv ID doesn't exist / points elsewhere | ❌ human |
| `arxiv-id-conflict` | ERROR | one entry carries two different arXiv IDs (text vs link) | ❌ human |
| `cited-not-in-bib` | ERROR | the manuscript cites a key the bib doesn't have | ❌ human |
| `issue-mismatch` | WARNING | issue number disagrees with the record | ✅ (`--fix-warnings`) |
| `journal-mismatch` / `author-mismatch` | WARNING | journal / first author disagree with the record | ❌ |
| `duplicate-key` | WARNING | the key is defined twice in the bib | ❌ |
| `possible-version-mismatch` | WARNING | title lookup hit something >1 year off (reprint? edition?) | ❌ |
| `unreliable-title-match` | WARNING | title lookup hit, but the first author doesn't match | ❌ |
| `doi-check-failed` / `arxiv-check-failed` | WARNING | network/rate-limit failure, not a 404 — try again | ❌ |
| `year-online-first` | WARNING | 1 year off the online-first date (often fine) | ❌ |
| `missing-doi` | INFO | journal article lacks a DOI (suggested value attached) | ✅ |
| `doi-url-prefix` | INFO | DOI field holds a full URL instead of the bare DOI | ✅ |
| `datacite-doi` | INFO | DOI lives in DataCite (figshare/Zenodo/…), confirmed to exist | ❌ |
| `published-version-available` | INFO | the arXiv preprint has a published version | ❌ |
| `not-cited` | INFO | entry not cited by the `--tex` documents, unchecked | ❌ |
| `not-found-in-databases` | WARNING | nothing found anywhere — needs human review | ❌ |

## Keeping false positives down

A checker that cries wolf gets muted, so a large share of the code exists to *not* complain. Journal names are stem-normalized (`Phys. Rev. Lett.` is `Physical Review Letters`). Fields missing on either side are never compared. Online-first offsets get ±1 year of slack when volume or pages agree. First-page-only citations pass silently (`pages={2863}` matches a record of `2863-2866`), as do leading zeros (`061` is `61`). Title-based lookups must clear a version gate and a first-author gate before any field gets compared, and DBLP's separate records for reprints (a NIPS 2012 original vs its CACM 2017 reprint) are disambiguated by year. Network errors and rate limits report "check failed, retry"; only a confirmed 404 is reported as missing.

## Tests

```bash
python tests/run_golden.py   # known-good bib stays clean; planted errors reproduce exactly
python tests/test_fix.py     # --fix repairs what's fixable; backup matches the original byte for byte
python tests/test_tex.py     # --tex filtering, ghost keys, comment handling
python tests/test_bbl.py     # .bbl parsing and planted-error reproduction
```

Fixtures are synthetic and use famous public papers, so expectations are stable: `ref_fixed.bib` must stay at zero findings, `ref_broken.bib` plants four errors plus a wrong issue number plus a missing DOI, and `sample.bbl` plants an arXiv-ID conflict and a truncated article number.

## Layout

```
refright/
  bibparser.py    .bib parsing (tolerates missing commas, empty values)
  bblparser.py    .bbl parsing (REVTeX markup + best-effort plain text)
  texscan.py      finds the keys actually \cite'd in .tex sources
  sources.py      Crossref / OpenAlex / DBLP / arXiv / DataCite clients
                  (sqlite cache, per-source rate limits, concurrency-safe)
  match.py        fuzzy matching for titles, journals, authors
  engine.py       the per-entry checking rules and false-positive gates
  fixer.py        the auto-fixer (dry-run diff, backups, surgical edits)
  report.py       terminal (compact/verbose) + JSON output
  report_html.py  the single-file HTML evidence report
  cli.py          the `refright` command
```

API responses are cached in `~/.cache/refright/cache.sqlite` for 7 days; `--no-cache` bypasses.

## License

MIT
