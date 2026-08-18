"""Verification engine: compares each bib entry against authoritative sources.

False-positive control rules (see README §Keeping false positives down):
- journal abbreviations are normalized before comparison
- fields missing on either side are skipped, never flagged
- online-first year differences (<=1 yr) pass when volume or pages match
- book-series volumes (Crossref often lacks them) are never compared
- article-number journals compare pages against article-number
- article numbers cited as in the DOI suffix (aat9004 vs record eaat9004) pass
"""
from __future__ import annotations

import html
import re

from .bibparser import BibEntry, extract_arxiv_id, extract_arxiv_ids
from .match import (first_surname, journal_match, norm_pages, norm_title,
                    title_sim)
from .models import EntryResult, FieldDiff, Finding, Severity
from .sources import Arxiv, Crossref, DataCite, DBLP, OpenAlex

TITLE_MATCH_THRESHOLD = 0.85


def _cr_years(cr: dict) -> set[int]:
    ys: set[int] = set()
    for f in ("issued", "published-print", "published-online", "published"):
        dp = (cr.get(f) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            ys.add(int(dp[0][0]))
    return ys


def _cr_summary(cr: dict) -> str:
    jrnl = (cr.get("container-title") or [""])[0]
    vol = cr.get("volume") or ""
    page = cr.get("page") or cr.get("article-number") or ""
    yrs = sorted(_cr_years(cr))
    return f"{jrnl} {vol}, {page} ({yrs[0] if yrs else '?'})"


def _article_num_equiv(bp: str, rp: str, doi: str) -> bool:
    """Article-number journals (Science Advances, Nature Communications, ...)
    are cited in divergent styles: pages={eaat9004}, pages={aat9004} (exactly
    as in the DOI suffix 10.1126/sciadv.aat9004), or with other leading-letter
    variants. Accept the bib value when it equals the DOI suffix, or when it
    differs from the publisher record only in leading letters."""
    strip = lambda s: s.lstrip("abcdefghijklmnopqrstuvwxyz")
    b, r_ = bp.lower(), rp.lower()
    suffix = (doi or "").lower().rsplit(".", 1)[-1]
    if suffix and "/" not in suffix and b == suffix:
        return True
    return bool(strip(b)) and strip(b) == strip(r_)


class Engine:
    def __init__(self, cache):
        self.crossref = Crossref(cache)
        self.dblp = DBLP(cache)
        self.openalex = OpenAlex(cache)
        self.arxiv = Arxiv(cache)
        self.datacite = DataCite(cache)

    # ---------- dispatch ----------

    def _check_one(self, e: BibEntry, aid: str | None, arxiv_data: dict,
                   arxiv_ok: bool = True) -> EntryResult:
        doi = e.fields.get("doi")
        if doi:
            return self.check_doi_entry(e, doi)
        if aid:
            r = self.check_arxiv_entry(e, aid, arxiv_data.get(aid), arxiv_ok)
            others = [x for x in extract_arxiv_ids(e) if x != aid]
            if others:
                r.findings.insert(0, Finding(
                    Severity.ERROR, "arxiv-id-conflict",
                    "two different arXiv ids in one entry (printed id and hyperlink disagree)",
                    evidence=[FieldDiff("arxiv", aid, " / ".join(others), "bib", "")]))
            return r
        return self.check_no_id_entry(e)

    def check_all(self, entries: list[BibEntry], workers: int = 6,
                  progress=None) -> list[EntryResult]:
        from collections import Counter
        dup_keys = {k for k, c in Counter(e.key for e in entries).items() if c > 1}

        arxiv_jobs = {e.key: extract_arxiv_id(e) for e in entries}
        arxiv_jobs = {k: v for k, v in arxiv_jobs.items() if v and not entries_by_key(entries, k).fields.get("doi")}
        arxiv_data, arxiv_status = self.arxiv.get_entries(sorted(set(arxiv_jobs.values())))
        arxiv_ok = arxiv_status != "error"

        total = len(entries)
        results: list[EntryResult | None] = [None] * total

        if workers <= 1:
            for i, e in enumerate(entries):
                r = self._check_one(e, arxiv_jobs.get(e.key), arxiv_data, arxiv_ok)
                results[i] = r
                if progress:
                    progress(i + 1, total, r)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(self._check_one, e, arxiv_jobs.get(e.key), arxiv_data, arxiv_ok): i
                        for i, e in enumerate(entries)}
                done = 0
                for fut in as_completed(futs):
                    i = futs[fut]
                    try:
                        results[i] = fut.result()
                    except Exception as exc:  # one failing entry must not kill the run
                        r = EntryResult(key=entries[i].key, entry_type=entries[i].entry_type,
                                        title=entries[i].title)
                        r.findings.append(Finding(Severity.WARNING, "check-failed",
                                                  f"check failed ({type(exc).__name__}); needs manual review"))
                        results[i] = r
                    done += 1
                    if progress:
                        progress(done, total, results[i])

        out = [r for r in results if r is not None]
        if dup_keys:
            for res in out:
                if res.key in dup_keys:
                    res.findings.insert(0, Finding(
                        Severity.WARNING, "duplicate-key",
                        "duplicate bib key: BibTeX uses only one of the definitions; merge them manually",
                        evidence=[FieldDiff("key", res.key, "defined more than once", "bib", "")]))
        return out

    # ---------- entry types ----------

    def check_doi_entry(self, e: BibEntry, doi: str) -> EntryResult:
        r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
        r.checked_by.append("crossref")
        clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi).strip()
        if clean != doi:
            r.findings.append(Finding(
                Severity.INFO, "doi-url-prefix",
                "the doi field holds a full URL; keep only the DOI itself",
                evidence=[FieldDiff("doi", doi, clean, "bib", "")],
                fix=FieldDiff("doi", doi, clean, "bib", "")))
            doi = clean
        doi_url = f"https://doi.org/{doi}"
        cr, status = self.crossref.get_work(doi)
        if cr is None:
            if status == "error":
                r.findings.append(Finding(
                    Severity.WARNING, "doi-check-failed",
                    "DOI check request failed (network error or rate limit); cannot confirm — retry later",
                    evidence=[FieldDiff("doi", doi, "request failed", "crossref", doi_url)]))
                return r
            f = Finding(Severity.ERROR, "doi-unresolvable",
                        "DOI does not resolve (HTTP 404)",
                        evidence=[FieldDiff("doi", doi, "HTTP 404 unresolvable", "crossref", doi_url)])
            # not all DOIs live in Crossref — figshare/Zenodo/… use DataCite
            dc, dc_status = self.datacite.get(doi)
            if dc is not None:
                r.checked_by.append("datacite")
                f = Finding(Severity.INFO, "datacite-doi",
                            "DOI is registered with DataCite (dataset/preprint repository); existence confirmed",
                            evidence=[FieldDiff("doi", doi, dc.get("title") or "exists",
                                                "datacite", doi_url)])
                if e.title and dc.get("title") and \
                        title_sim(e.title, dc["title"]) < TITLE_MATCH_THRESHOLD:
                    r.findings.append(Finding(
                        Severity.WARNING, "title-mismatch",
                        "title does not match the resource this DOI points to (DOI may belong to a different work)",
                        evidence=[FieldDiff("title", e.title, dc["title"], "datacite", doi_url)]))
                    return r
            else:
                cand = self._reverse_lookup(e.title)
                if cand:
                    f.fix = FieldDiff("doi", doi, cand.get("DOI", ""),
                                      "crossref-search", f"https://doi.org/{cand.get('DOI')}")
                    f.suggestion = f"reverse lookup by title found: {_cr_summary(cand)}"
            r.findings.append(f)
            return r
        self._compare_with_crossref(e, cr, r, doi_url)
        return r

    def check_arxiv_entry(self, e: BibEntry, aid: str, rec: dict | None,
                          arxiv_ok: bool = True) -> EntryResult:
        r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
        r.checked_by.append("arxiv")
        url = f"https://arxiv.org/abs/{aid}"
        if rec is None:
            if not arxiv_ok:
                r.findings.append(Finding(
                    Severity.WARNING, "arxiv-check-failed",
                    "arXiv API request failed (network error / rate limit); cannot confirm the id — retry later",
                    evidence=[FieldDiff("arxiv", aid, "request failed", "arxiv", url)]))
                return r
            r.findings.append(Finding(
                Severity.ERROR, "arxiv-id-not-found",
                "arXiv id does not exist",
                evidence=[FieldDiff("arxiv", aid, "not found", "arxiv", url)]))
            return r
        if title_sim(e.title, rec["title"]) < TITLE_MATCH_THRESHOLD:
            r.findings.append(Finding(
                Severity.ERROR, "arxiv-id-mismatch",
                "the arXiv id points to a paper with a different title",
                evidence=[FieldDiff("title", e.title, rec["title"], "arxiv", url)]))
            return r
        if rec.get("doi") or rec.get("journal_ref"):
            r.findings.append(Finding(
                Severity.INFO, "published-version-available",
                "a published version exists; consider updating the entry",
                evidence=[FieldDiff("status", "preprint",
                                    rec.get("journal_ref") or rec.get("doi"), "arxiv", url)]))
        bib_year = e.fields.get("year", "")
        if bib_year and rec.get("published"):
            if abs(int(bib_year) - int(rec["published"][:4])) > 1:
                r.findings.append(Finding(
                    Severity.WARNING, "arxiv-year-mismatch",
                    "year differs from the arXiv submission year by more than 1",
                    evidence=[FieldDiff("year", bib_year, rec["published"][:4], "arxiv", url)]))
        return r

    def check_no_id_entry(self, e: BibEntry) -> EntryResult:
        r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
        if e.entry_type == "article" and e.fields.get("journal"):
            r.checked_by.append("crossref-search")
            f = Finding(Severity.INFO, "missing-doi", "journal article has no DOI field")
            cand = self._reverse_lookup(e.title)
            if cand:
                url = f"https://doi.org/{cand.get('DOI')}"
                if self._reverse_gates(e, cand, r, url):
                    f.suggestion = f"title-matched record (possibly a different edition/reprint): {_cr_summary(cand)}"
                else:
                    f.fix = FieldDiff("doi", "(missing)", cand.get("DOI", ""),
                                      "crossref-search", url)
                    f.suggestion = f"title-matched record: {_cr_summary(cand)}"
                    self._compare_with_crossref(e, cand, r, url,
                                                skip_title=True, relaxed=True)
            r.findings.append(f)
            return r
        # conference papers / other: DBLP first, Crossref fallback
        r.checked_by.extend(["dblp", "crossref-search"])
        hit = self._dblp_lookup(e.title, e.fields.get("author", ""), e.fields.get("year", ""))
        if hit:
            url = hit.get("url", "")
            bp = norm_pages(e.fields.get("pages", ""))
            hp = norm_pages(hit.get("pages", ""))
            if bp and hp and bp != hp:
                r.findings.append(Finding(
                    Severity.ERROR, "pages-mismatch",
                    "pages do not match the DBLP record",
                    evidence=[FieldDiff("pages", e.fields.get("pages", ""), hp, "dblp", url)],
                    fix=FieldDiff("pages", e.fields.get("pages", ""), hp, "dblp", url)))
            by, hy = e.fields.get("year", ""), str(hit.get("year", ""))
            if by and hy and abs(int(by) - int(hy)) > 1:
                r.findings.append(Finding(
                    Severity.ERROR, "year-mismatch",
                    "year does not match the DBLP record",
                    evidence=[FieldDiff("year", by, hy, "dblp", url)],
                    fix=FieldDiff("year", by, hy, "dblp", url)))
            return r
        cand = self._reverse_lookup(e.title)
        if cand:
            url = f"https://doi.org/{cand.get('DOI')}"
            if self._reverse_gates(e, cand, r, url):
                return r
            self._compare_with_crossref(e, cand, r, url,
                                        skip_title=True, relaxed=True)
            if not r.findings:
                r.findings.append(Finding(Severity.INFO, "identified-via-title",
                                          "id-less entry matched a Crossref record by title; metadata is consistent",
                                          evidence=[FieldDiff("record", "(no doi)",
                                                              _cr_summary(cand), "crossref-search",
                                                              url)]))
            return r
        r.findings.append(Finding(Severity.WARNING, "not-found-in-databases",
                                  "not found in Crossref/DBLP; needs manual review"))
        return r

    # ---------- field comparison against a Crossref record ----------

    def _compare_with_crossref(self, e: BibEntry, cr: dict, r: EntryResult,
                               url: str, skip_title: bool = False,
                               relaxed: bool = False) -> None:
        """relaxed=True is used for title-based reverse lookups: journal, volume,
        issue and first-author are not compared (proceedings records often carry
        year-as-volume garbage, preprint-style journal strings and same-title
        different-paper hits would otherwise false-positive). Pages and year
        stay checked."""
        f = e.fields
        cr_title = html.unescape((cr.get("title") or [""])[0])
        if not skip_title and f.get("title") and title_sim(f["title"], cr_title) < TITLE_MATCH_THRESHOLD:
            r.findings.append(Finding(
                Severity.ERROR, "title-mismatch",
                "the DOI points to a paper with a different title (possible mix-up)",
                evidence=[FieldDiff("title", f["title"], cr_title, "crossref", url)]))
            return  # DOI points elsewhere; further field comparison is meaningless

        cr_j = html.unescape((cr.get("container-title") or [""])[0])
        if (not relaxed and e.entry_type == "article" and f.get("journal") and cr_j
                and not journal_match(f["journal"], cr_j)):
            r.findings.append(Finding(
                Severity.WARNING, "journal-mismatch", "journal name does not match the record",
                evidence=[FieldDiff("journal", f["journal"], cr_j, "crossref", url)]))

        vol_matched = True
        cr_vol = str(cr.get("volume") or "")
        if not relaxed and f.get("volume") and cr_vol:
            if str(f["volume"]) != cr_vol:
                vol_matched = False
                r.findings.append(Finding(
                    Severity.ERROR, "volume-mismatch",
                    "volume does not match the publisher record",
                    evidence=[FieldDiff("volume", str(f["volume"]), cr_vol, "crossref", url)],
                    fix=FieldDiff("volume", str(f["volume"]), cr_vol, "crossref", url)))

        cr_issue = str(cr.get("issue") or "")
        issue_key = "issue" if "issue" in f else "number"  # BibTeX spells "issue" as "number"
        bib_issue = str(f.get(issue_key) or "")
        if not relaxed and bib_issue and cr_issue and bib_issue != cr_issue:
            r.findings.append(Finding(
                Severity.WARNING, "issue-mismatch",
                "issue does not match the publisher record",
                evidence=[FieldDiff(issue_key, bib_issue, cr_issue, "crossref", url)],
                fix=FieldDiff(issue_key, bib_issue, cr_issue, "crossref", url)))

        page_matched = True
        cr_page = str(cr.get("page") or cr.get("article-number") or "")
        if f.get("pages") and cr_page:
            bp, rp = norm_pages(f["pages"]), norm_pages(cr_page)
            if bp != rp:
                bib_first, ref_first = bp.split("-")[0], rp.split("-")[0]
                if "-" not in bp and bp == ref_first:
                    pass  # first-page-only citation style: pages={2863} vs 2863-2866
                elif "-" not in rp and bib_first == rp:
                    pass  # record lists only the first page: 255--282 vs 255
                elif _article_num_equiv(bp, rp, f.get("doi", "")):
                    pass  # article number as in DOI suffix: aat9004 vs record eaat9004
                else:
                    page_matched = False
                    r.findings.append(Finding(
                        Severity.ERROR, "pages-mismatch",
                        "pages do not match the publisher record",
                        evidence=[FieldDiff("pages", f["pages"], cr_page, "crossref", url)],
                        fix=FieldDiff("pages", f["pages"], cr_page, "crossref", url)))

        by = f.get("year", "")
        if by:
            yrs = _cr_years(cr)
            bib_y = int(by)
            if yrs and bib_y not in yrs:
                diff = min(abs(bib_y - y) for y in yrs)
                if diff <= 1 and (vol_matched or page_matched) and (cr_vol or cr_page):
                    pass  # online-first offset, otherwise consistent
                elif diff <= 1:
                    r.findings.append(Finding(
                        Severity.WARNING, "year-online-first",
                        "year is 1 off the online-first year (may be the formal issue year — please confirm)",
                        evidence=[FieldDiff("year", by, "/".join(map(str, sorted(yrs))),
                                            "crossref", url)]))
                else:
                    target = next((str((cr.get(f) or {})["date-parts"][0][0])
                                   for f in ("published-print", "issued", "published", "published-online")
                                   if (cr.get(f) or {}).get("date-parts")
                                   and cr[f]["date-parts"][0] and cr[f]["date-parts"][0][0]), "")
                    r.findings.append(Finding(
                        Severity.ERROR, "year-mismatch",
                        "year does not match the publisher record",
                        evidence=[FieldDiff("year", by, "/".join(map(str, sorted(yrs))),
                                            "crossref", url)],
                        fix=FieldDiff("year", by, target, "crossref", url) if target else None))

        cr_authors = cr.get("author") or []
        if not relaxed and f.get("author") and cr_authors:
            # publisher data occasionally puts the full name into `family`
            # (e.g. "Xiao Han"); accept containment either way.
            bib_sur = first_surname(f["author"])
            ref_fam = norm_title(cr_authors[0].get("family", "")).replace(" ", "")
            if bib_sur and ref_fam and bib_sur not in ref_fam and ref_fam not in bib_sur:
                r.findings.append(Finding(
                    Severity.WARNING, "author-mismatch", "first-author surname does not match the record",
                    evidence=[FieldDiff("author", f["author"].split(" and ")[0],
                                        cr_authors[0].get("family", ""), "crossref", url)]))

    # ---------- reverse lookups ----------

    def _reverse_gates(self, e: BibEntry, cand: dict, r: EntryResult, url: str) -> bool:
        """Gates for title-based reverse-lookup candidates. Returns True when the
        candidate must NOT be used for field-by-field comparison:
        - version gate: bib year differs from every record year by >1
          (likely a reprint / book-chapter / different edition);
        - author gate: first-author surname clearly disagrees
          (likely a same-title different paper).
        Each gate emits a WARNING so the user still sees what was matched."""
        by = e.fields.get("year", "")
        if by.isdigit():
            yrs = _cr_years(cand)
            if yrs and min(abs(int(by) - y) for y in yrs) > 1:
                r.findings.append(Finding(
                    Severity.WARNING, "possible-version-mismatch",
                    "title-matched record differs in year by more than 1 (reprint or different version?) — confirm manually",
                    evidence=[FieldDiff("year", by, "/".join(map(str, sorted(yrs))),
                                        "crossref-search", url)]))
                return True
        bib_sur = first_surname(e.fields.get("author", ""))
        cr_authors = cand.get("author") or []
        if bib_sur and cr_authors:
            ref_fam = norm_title(cr_authors[0].get("family", "")).replace(" ", "")
            if ref_fam and bib_sur not in ref_fam and ref_fam not in bib_sur:
                r.findings.append(Finding(
                    Severity.WARNING, "unreliable-title-match",
                    "title-matched record has a different first author (same-title different paper?) — review manually",
                    evidence=[FieldDiff("author", e.fields["author"].split(" and ")[0],
                                        cr_authors[0].get("family", ""),
                                        "crossref-search", url)]))
                return True
        return False

    def _reverse_lookup(self, title: str) -> dict | None:
        """Crossref bibliographic search first; OpenAlex fallback (better ranking for
        generic titles). Always returns a Crossref record (verified by title sim)."""
        items = self.crossref.search_by_title(title)
        if items:
            best, best_sim = None, 0.0
            for it in items:
                s = title_sim(title, (it.get("title") or [""])[0])
                if s > best_sim:
                    best, best_sim = it, s
            if best_sim >= TITLE_MATCH_THRESHOLD:
                return best
        for hit in self.openalex.search(title):
            if hit["doi"] and title_sim(title, hit["title"]) >= TITLE_MATCH_THRESHOLD:
                cr, _st = self.crossref.get_work(hit["doi"])
                if cr and title_sim(title, (cr.get("title") or [""])[0]) >= TITLE_MATCH_THRESHOLD:
                    return cr
        return None

    def _dblp_lookup(self, title: str, author: str = "", year: str = "") -> dict | None:
        """Best DBLP hit for a title. DBLP keeps separate records per version
        (conference vs journal reprint), so when several candidates pass the
        title threshold the one closest to the bib year wins — otherwise a
        famous paper's reprint (e.g. CACM 2017 vs NIPS 2012) false-alarms."""
        queries = [title]
        sur = first_surname(author) if author else ""
        if sur:
            queries.append(f"{title} {sur}")  # DBLP ranks much better with a surname
        cands: list[tuple[float, dict]] = []
        for q in queries:
            for info in self.dblp.search(q):
                s = title_sim(title, info.get("title", ""))
                if s >= TITLE_MATCH_THRESHOLD:
                    cands.append((s, info))
            if cands:
                break
        if not cands:
            return None
        if year.isdigit():
            by = int(year)
            consistent = [c for c in cands
                          if str(c[1].get("year", "")).isdigit()
                          and abs(int(c[1]["year"]) - by) <= 1]
            if consistent:
                return max(consistent, key=lambda c: c[0])[1]
        return max(cands, key=lambda c: c[0])[1]


def entries_by_key(entries: list[BibEntry], key: str) -> BibEntry:
    return next(e for e in entries if e.key == key)
