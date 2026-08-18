"""Verification engine: compares each bib entry against authoritative sources.

False-positive control rules (see README §误报控制):
- journal abbreviations are normalized before comparison
- fields missing on either side are skipped, never flagged
- online-first year differences (<=1 yr) pass when volume or pages match
- book-series volumes (Crossref often lacks them) are never compared
- article-number journals compare pages against article-number
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
                    "条目中出现两个不同的 arXiv 编号（正文编号与链接不一致）",
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
                                                  f"核查过程出错（{type(exc).__name__}），需人工核查"))
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
                        "该引用 key 在 bib 中重复定义，BibTeX 只会使用其中一个，需人工合并",
                        evidence=[FieldDiff("key", res.key, "重复定义", "bib", "")]))
        return out

    # ---------- entry types ----------

    def check_doi_entry(self, e: BibEntry, doi: str) -> EntryResult:
        r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
        r.checked_by.append("crossref")
        clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi).strip()
        if clean != doi:
            r.findings.append(Finding(
                Severity.INFO, "doi-url-prefix",
                "DOI 字段写成了完整 URL，建议只保留 DOI 本体",
                evidence=[FieldDiff("doi", doi, clean, "bib", "")],
                fix=FieldDiff("doi", doi, clean, "bib", "")))
            doi = clean
        doi_url = f"https://doi.org/{doi}"
        cr, status = self.crossref.get_work(doi)
        if cr is None:
            if status == "error":
                r.findings.append(Finding(
                    Severity.WARNING, "doi-check-failed",
                    "DOI 核查请求失败（网络错误或限流），无法确认，建议稍后重试",
                    evidence=[FieldDiff("doi", doi, "request failed", "crossref", doi_url)]))
                return r
            f = Finding(Severity.ERROR, "doi-unresolvable",
                        "DOI 无法解析（HTTP 404）",
                        evidence=[FieldDiff("doi", doi, "HTTP 404 无法解析", "crossref", doi_url)])
            # not all DOIs live in Crossref — figshare/Zenodo/… use DataCite
            dc, dc_status = self.datacite.get(doi)
            if dc is not None:
                r.checked_by.append("datacite")
                f = Finding(Severity.INFO, "datacite-doi",
                            "DOI 注册在 DataCite（数据集/预印本仓库），已确认存在",
                            evidence=[FieldDiff("doi", doi, dc.get("title") or "存在",
                                                "datacite", doi_url)])
                if e.title and dc.get("title") and \
                        title_sim(e.title, dc["title"]) < TITLE_MATCH_THRESHOLD:
                    r.findings.append(Finding(
                        Severity.WARNING, "title-mismatch",
                        "DOI 指向的资源与条目标题不符（DOI 可能张冠李戴）",
                        evidence=[FieldDiff("title", e.title, dc["title"], "datacite", doi_url)]))
                    return r
            else:
                cand = self._reverse_lookup(e.title)
                if cand:
                    f.fix = FieldDiff("doi", doi, cand.get("DOI", ""),
                                      "crossref-search", f"https://doi.org/{cand.get('DOI')}")
                    f.suggestion = f"按标题反查到正确记录：{_cr_summary(cand)}"
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
                    "arXiv API 请求失败（网络错误/限流），无法确认编号，建议稍后重试",
                    evidence=[FieldDiff("arxiv", aid, "request failed", "arxiv", url)]))
                return r
            r.findings.append(Finding(
                Severity.ERROR, "arxiv-id-not-found",
                "arXiv 编号不存在",
                evidence=[FieldDiff("arxiv", aid, "not found", "arxiv", url)]))
            return r
        if title_sim(e.title, rec["title"]) < TITLE_MATCH_THRESHOLD:
            r.findings.append(Finding(
                Severity.ERROR, "arxiv-id-mismatch",
                "arXiv 编号指向的论文与条目标题不符",
                evidence=[FieldDiff("title", e.title, rec["title"], "arxiv", url)]))
            return r
        if rec.get("doi") or rec.get("journal_ref"):
            r.findings.append(Finding(
                Severity.INFO, "published-version-available",
                "已有正式出版版本，建议更新条目",
                evidence=[FieldDiff("status", "preprint",
                                    rec.get("journal_ref") or rec.get("doi"), "arxiv", url)]))
        bib_year = e.fields.get("year", "")
        if bib_year and rec.get("published"):
            if abs(int(bib_year) - int(rec["published"][:4])) > 1:
                r.findings.append(Finding(
                    Severity.WARNING, "arxiv-year-mismatch",
                    "年份与 arXiv 提交年份相差超过 1 年",
                    evidence=[FieldDiff("year", bib_year, rec["published"][:4], "arxiv", url)]))
        return r

    def check_no_id_entry(self, e: BibEntry) -> EntryResult:
        r = EntryResult(key=e.key, entry_type=e.entry_type, title=e.title)
        if e.entry_type == "article" and e.fields.get("journal"):
            r.checked_by.append("crossref-search")
            f = Finding(Severity.INFO, "missing-doi", "期刊论文缺少 DOI 字段")
            cand = self._reverse_lookup(e.title)
            if cand:
                url = f"https://doi.org/{cand.get('DOI')}"
                if self._reverse_gates(e, cand, r, url):
                    f.suggestion = f"按标题匹配到记录（可能为不同版本/重印）：{_cr_summary(cand)}"
                else:
                    f.fix = FieldDiff("doi", "(缺失)", cand.get("DOI", ""),
                                      "crossref-search", url)
                    f.suggestion = f"按标题匹配到记录：{_cr_summary(cand)}"
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
                    "页码与 DBLP 记录不符",
                    evidence=[FieldDiff("pages", e.fields.get("pages", ""), hp, "dblp", url)],
                    fix=FieldDiff("pages", e.fields.get("pages", ""), hp, "dblp", url)))
            by, hy = e.fields.get("year", ""), str(hit.get("year", ""))
            if by and hy and abs(int(by) - int(hy)) > 1:
                r.findings.append(Finding(
                    Severity.ERROR, "year-mismatch",
                    "年份与 DBLP 记录不符",
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
                                          "无标识条目已通过标题匹配到 Crossref 记录，元数据一致",
                                          evidence=[FieldDiff("record", "(no doi)",
                                                              _cr_summary(cand), "crossref-search",
                                                              url)]))
            return r
        r.findings.append(Finding(Severity.WARNING, "not-found-in-databases",
                                  "未在 Crossref/DBLP 检到此条目，需人工核查"))
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
                "DOI 指向的论文与条目标题不符（DOI 可能张冠李戴）",
                evidence=[FieldDiff("title", f["title"], cr_title, "crossref", url)]))
            return  # DOI points elsewhere; further field comparison is meaningless

        cr_j = html.unescape((cr.get("container-title") or [""])[0])
        if (not relaxed and e.entry_type == "article" and f.get("journal") and cr_j
                and not journal_match(f["journal"], cr_j)):
            r.findings.append(Finding(
                Severity.WARNING, "journal-mismatch", "期刊名与记录不符",
                evidence=[FieldDiff("journal", f["journal"], cr_j, "crossref", url)]))

        vol_matched = True
        cr_vol = str(cr.get("volume") or "")
        if not relaxed and f.get("volume") and cr_vol:
            if str(f["volume"]) != cr_vol:
                vol_matched = False
                r.findings.append(Finding(
                    Severity.ERROR, "volume-mismatch",
                    "卷号与出版方记录不符",
                    evidence=[FieldDiff("volume", str(f["volume"]), cr_vol, "crossref", url)],
                    fix=FieldDiff("volume", str(f["volume"]), cr_vol, "crossref", url)))

        cr_issue = str(cr.get("issue") or "")
        issue_key = "issue" if "issue" in f else "number"  # BibTeX 用 number 表期号
        bib_issue = str(f.get(issue_key) or "")
        if not relaxed and bib_issue and cr_issue and bib_issue != cr_issue:
            r.findings.append(Finding(
                Severity.WARNING, "issue-mismatch",
                "期号与出版方记录不符",
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
                else:
                    page_matched = False
                    r.findings.append(Finding(
                        Severity.ERROR, "pages-mismatch",
                        "页码与出版方记录不符",
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
                        "年份与在线首发年份相差 1 年（可能为正式卷期年份，请确认）",
                        evidence=[FieldDiff("year", by, "/".join(map(str, sorted(yrs))),
                                            "crossref", url)]))
                else:
                    target = next((str((cr.get(f) or {})["date-parts"][0][0])
                                   for f in ("published-print", "issued", "published", "published-online")
                                   if (cr.get(f) or {}).get("date-parts")
                                   and cr[f]["date-parts"][0] and cr[f]["date-parts"][0][0]), "")
                    r.findings.append(Finding(
                        Severity.ERROR, "year-mismatch",
                        "年份与出版方记录不符",
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
                    Severity.WARNING, "author-mismatch", "第一作者姓氏与记录不符",
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
                    "标题匹配到的记录年份相差超过 1 年，可能是重印版或不同版本，请人工确认",
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
                    "标题匹配到的记录第一作者不符，可能是同名不同论文，请人工核查",
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
