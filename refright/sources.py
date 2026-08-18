"""Data sources: Crossref, OpenAlex, DBLP, arXiv.

Stdlib urllib only, sqlite-cached, rate-limited, and safe for concurrent use.
Each source has its own RateLimiter: a semaphore caps concurrent in-flight
requests and a shared clock enforces a minimum interval between request starts
(so N workers overlap network latency without hammering the API).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .cache import Cache

_UA = {"User-Agent": "refright/0.2 (bibliography verification; mailto:refright@example.com)"}


class RateLimiter:
    def __init__(self, min_interval: float, max_concurrent: int = 1):
        self.min_interval = min_interval
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._last = 0.0

    def __enter__(self):
        self._sem.acquire()
        with self._lock:
            dt = time.time() - self._last
            if dt < self.min_interval:
                time.sleep(self.min_interval - dt)
            self._last = time.time()
        return self

    def __exit__(self, *exc):
        self._sem.release()
        return False


def _http_get(url: str, cache: Cache, limiter: RateLimiter) -> tuple[str, str | None]:
    """Fetch with cache + rate limiting.

    Returns (status, body): status is "ok", "not_found" (confirmed 404, cached
    as ''), or "error" (network failure/timeout/5xx — never cached, so a later
    run can retry). A 429 is retried once after a 2 s backoff.
    """
    hit = cache.get(url)
    if hit is not None:
        return ("not_found", None) if hit == "" else ("ok", hit)
    for attempt in range(2):
        with limiter:
            req = urllib.request.Request(url, headers=_UA)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    cache.put(url, "")
                    return "not_found", None
                if e.code == 429 and attempt == 0:
                    time.sleep(2.0)
                    continue
                return "error", None
            except Exception:
                if attempt == 0:
                    continue
                return "error", None
        cache.put(url, body)
        return "ok", body
    return "error", None


class Crossref:
    def __init__(self, cache: Cache):
        self.cache = cache
        self.limiter = RateLimiter(min_interval=0.05, max_concurrent=6)

    def get_work(self, doi: str) -> tuple[dict | None, str]:
        """Returns (record, status); status is 'ok', 'not_found', or 'error'."""
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
        status, body = _http_get(url, self.cache, self.limiter)
        if status != "ok" or not body:
            return None, status
        try:
            return json.loads(body)["message"], "ok"
        except Exception:
            return None, "error"

    def search_by_title(self, title: str, rows: int = 5) -> list[dict] | None:
        """Candidates by bibliographic search; caller applies similarity threshold."""
        q = urllib.parse.quote_plus(title)
        url = f"https://api.crossref.org/works?query.bibliographic={q}&rows={rows}"
        status, body = _http_get(url, self.cache, self.limiter)
        if status != "ok" or not body:
            return None
        try:
            items = json.loads(body)["message"]["items"]
        except Exception:
            return None
        return items or None


class DataCite:
    """Fallback for DOIs not registered with Crossref (figshare, Zenodo, …)."""

    def __init__(self, cache: Cache):
        self.cache = cache
        self.limiter = RateLimiter(min_interval=0.1, max_concurrent=4)

    def get(self, doi: str) -> tuple[dict | None, str]:
        """Returns ({title, year}, status); status is 'ok', 'not_found', or 'error'."""
        url = "https://api.datacite.org/dois/" + urllib.parse.quote(doi)
        status, body = _http_get(url, self.cache, self.limiter)
        if status != "ok" or not body:
            return None, status
        try:
            attrs = json.loads(body)["data"]["attributes"]
            titles = attrs.get("titles") or []
            return {"title": titles[0].get("title", "") if titles else "",
                    "year": attrs.get("publicationYear")}, "ok"
        except Exception:
            return None, "error"


class OpenAlex:
    """Relevance-ranked title search — much stronger than Crossref for generic titles."""

    def __init__(self, cache: Cache):
        self.cache = cache
        self.limiter = RateLimiter(min_interval=0.1, max_concurrent=4)

    def search(self, title: str, rows: int = 5) -> list[dict]:
        """Returns [{doi, title, year}]."""
        q = urllib.parse.quote_plus(title)
        url = f"https://api.openalex.org/works?search={q}&per-page={rows}"
        status, body = _http_get(url, self.cache, self.limiter)
        if status != "ok" or not body:
            return []
        out = []
        try:
            for w in json.loads(body).get("results", []):
                doi = (w.get("doi") or "").replace("https://doi.org/", "")
                out.append({"doi": doi, "title": w.get("display_name") or "",
                            "year": w.get("publication_year")})
        except Exception:
            return []
        return out


class DBLP:
    def __init__(self, cache: Cache):
        self.cache = cache
        self.limiter = RateLimiter(min_interval=0.5, max_concurrent=2)

    def search(self, title: str, rows: int = 5) -> list[dict] | None:
        """None = request failed (outage/rate limit); [] = genuine no-hit.
        Callers must distinguish the two — an outage is not an empty index."""
        q = urllib.parse.quote_plus(re.sub(r"[^A-Za-z0-9 ]", " ", title))
        url = f"https://dblp.org/search/publ/api?q={q}&format=json&h={rows}"
        status, body = _http_get(url, self.cache, self.limiter)
        if status != "ok" or not body:
            return None
        try:
            hits = json.loads(body)["result"]["hits"].get("hit", [])
            return [h["info"] for h in hits]
        except Exception:
            return None


class Arxiv:
    def __init__(self, cache: Cache):
        self.cache = cache
        self.limiter = RateLimiter(min_interval=3.0, max_concurrent=1)

    def get_entries(self, ids: list[str]) -> tuple[dict[str, dict], str]:
        """Batch fetch; returns ({id: {title, authors, published, journal_ref, doi}},
        status). status 'error' means the batch request itself failed — callers
        must not treat missing ids as non-existent in that case."""
        if not ids:
            return {}, "ok"
        url = ("https://export.arxiv.org/api/query?id_list="
               + ",".join(ids) + f"&max_results={len(ids) + 2}")
        status, body = _http_get(url, self.cache, self.limiter)
        if status != "ok" or not body:
            return {}, status
        out: dict[str, dict] = {}
        for e in re.findall(r"<entry>(.*?)</entry>", body, re.S):
            mid = re.search(r"<id>http://arxiv.org/abs/([^<]+)</id>", e)
            if not mid:
                continue
            aid = re.sub(r"v\d+$", "", mid.group(1))
            title_m = re.search(r"<title>(.*?)</title>", e, re.S)
            pub_m = re.search(r"<published>([^<]+)</published>", e)
            jr_m = re.search(r'<arxiv:journal_ref[^>]*>([^<]+)</arxiv:journal_ref>', e)
            doi_m = re.search(r'<arxiv:doi[^>]*>([^<]+)</arxiv:doi>', e)
            authors = re.findall(r"<name>([^<]+)</name>", e)
            out[aid] = {
                "title": re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else "",
                "published": pub_m.group(1)[:10] if pub_m else "",
                "journal_ref": jr_m.group(1) if jr_m else "",
                "doi": doi_m.group(1) if doi_m else "",
                "first_author": authors[0] if authors else "",
            }
        return out, "ok"
