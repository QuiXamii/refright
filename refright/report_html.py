"""Single-file HTML report — the human-review trust layer.

Design goals:
- every finding carries field-level evidence (bib value vs database value)
- one-click verification links (doi.org / arxiv.org / dblp.org)
- provenance: each entry shows which sources checked it
- passing entries collapsed, not hidden — a reviewer can audit them too
- pure static file, no server, safe to email to collaborators
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from . import __version__
from .models import EntryResult, Severity

_SEV_NAME = {Severity.ERROR: "error", Severity.WARNING: "warning",
             Severity.INFO: "info", Severity.OK: "ok"}
_SEV_LABEL = {Severity.ERROR: "❌ ERROR", Severity.WARNING: "⚠️ WARNING",
              Severity.INFO: "ℹ️ INFO", Severity.OK: "✅ OK"}

CSS = """
:root{--err:#dc2626;--warn:#d97706;--info:#2563eb;--ok:#16a34a;--ink:#1f2937;--mut:#6b7280;--line:#e5e7eb;--bg:#f8fafc}
*{box-sizing:border-box}body{margin:0;font:15px/1.6 -apple-system,"PingFang SC","Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:14px 24px;z-index:9}
h1{font-size:20px;margin:0 0 4px}.meta{color:var(--mut);font-size:12.5px}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
.chip{border:1.5px solid var(--line);border-radius:999px;padding:3px 14px;cursor:pointer;background:#fff;font-size:13.5px;font-weight:600;user-select:none}
.chip.on[data-sev=error]{border-color:var(--err);color:var(--err)}.chip.on[data-sev=warning]{border-color:var(--warn);color:var(--warn)}
.chip.on[data-sev=info]{border-color:var(--info);color:var(--info)}.chip.on[data-sev=ok]{border-color:var(--ok);color:var(--ok)}
.chip.on[data-sev=all]{border-color:var(--ink)}
#q{margin-top:10px;width:100%;max-width:420px;padding:6px 12px;border:1.5px solid var(--line);border-radius:8px;font-size:14px}
main{max-width:960px;margin:22px auto 60px;padding:0 20px}
.card{background:#fff;border:1px solid var(--line);border-left:5px solid var(--ok);border-radius:10px;padding:14px 18px;margin:14px 0;box-shadow:0 1px 2px rgb(0 0 0/.04)}
.card.sev-error{border-left-color:var(--err)}.card.sev-warning{border-left-color:var(--warn)}.card.sev-info{border-left-color:var(--info)}
.chead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.key{font:600 15px ui-monospace,Menlo,monospace}
.badge{font-size:11.5px;color:var(--mut);border:1px solid var(--line);border-radius:5px;padding:1px 7px}
.sevchip{margin-left:auto;font-weight:700;font-size:13px}
.sev-error>.chead>.sevchip{color:var(--err)}.sev-warning>.chead>.sevchip{color:var(--warn)}
.sev-info>.chead>.sevchip{color:var(--info)}.sev-ok>.chead>.sevchip{color:var(--ok)}
.title{margin:6px 0 2px;font-size:14.5px}.prov{font-size:12px;color:var(--mut);margin-top:4px}
.bibmeta{font:12.5px ui-monospace,Menlo,monospace;color:var(--mut);margin-top:6px;background:#f9fafb;border-radius:6px;padding:6px 10px;overflow-x:auto;white-space:nowrap}
.finding{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
.fmsg{font-weight:600;font-size:14px}
table.ev{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
table.ev th{background:#f3f4f6;text-align:left;padding:5px 9px;font-weight:600}
table.ev td{border-top:1px solid var(--line);padding:5px 9px;vertical-align:top}
table.ev code{font:12.5px ui-monospace,Menlo,monospace}
td.bibv{color:#991b1b}td.refv{color:#065f46}
.src{font-size:11.5px;border:1px solid var(--line);border-radius:5px;padding:0 6px;color:var(--mut);white-space:nowrap}
a{color:var(--info);text-decoration:none}a:hover{text-decoration:underline}
.sug{margin-top:8px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:8px 12px;font-size:13.5px}
.fix{margin-top:10px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:9px 13px;font-size:14px}
.fix .flabel{font-weight:700;font-size:13px;color:#92400e;margin-right:8px}
.fix code{font:13px ui-monospace,Menlo,monospace;padding:1px 6px;border-radius:5px}
.fix .old{color:#991b1b;background:#fee2e2;text-decoration:line-through}
.fix .new{color:#166534;background:#dcfce7;font-weight:700}
.fix .arrow{color:var(--mut);margin:0 6px;font-weight:700}
.fix .fname{color:var(--mut);font-size:12.5px;margin-right:4px}
.fix .note{color:var(--mut);font-size:12.5px;margin-top:5px}
details.card summary{list-style:none;cursor:pointer}details.card summary::-webkit-details-marker{display:none}
details.card summary::after{content:"▸ Show check details";color:var(--mut);font-size:12px;margin-top:2px}
details.card[open] summary::after{content:"▾ Collapse"}
.hidden{display:none!important}
footer{text-align:center;color:var(--mut);font-size:12px;margin:30px 0}
"""

JS = """
let sev='all';
function setSev(s,el){sev=s;document.querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));el.classList.add('on');apply();}
function apply(){
  const q=document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('main .card').forEach(c=>{
    const okSev=(sev==='all'||c.dataset.sev===sev);
    const okQ=(!q||c.dataset.key.includes(q)||(c.dataset.title||'').includes(q));
    c.classList.toggle('hidden',!(okSev&&okQ));});
}
document.getElementById('q').addEventListener('input',apply);
"""


def _esc(s: object) -> str:
    return html.escape(str(s or ""))


def _bibmeta(r: EntryResult, entry) -> str:
    parts = []
    for k in ("author", "journal", "booktitle", "volume", "issue", "pages", "year", "doi", "url"):
        v = entry.fields.get(k)
        if v:
            parts.append(f"{k}={v}")
    return _esc("  ".join(parts))


def render_html(results: list[EntryResult], entries, bib_path: str,
                tex_note: str = "") -> str:
    by_key = {e.key: e for e in entries}
    counts = {s: 0 for s in Severity}
    for r in results:
        counts[r.severity] += 1

    cards = []
    for r in sorted(results, key=lambda x: -x.severity):
        e = by_key.get(r.key)
        sev = _SEV_NAME[r.severity]
        title = _esc(r.title)
        findings = []
        for f in r.findings:
            rows = []
            for ev in f.evidence:
                link = f'<a href="{_esc(ev.url)}" target="_blank" rel="noopener">verify ↗</a>' if ev.url else ""
                rows.append(
                    f'<tr><td><code>{_esc(ev.field)}</code></td>'
                    f'<td class="bibv"><code>{_esc(ev.bib_value)}</code></td>'
                    f'<td class="refv"><code>{_esc(ev.ref_value)}</code></td>'
                    f'<td><span class="src">{_esc(ev.source)}</span></td><td>{link}</td></tr>')
            dup = (f.fix is not None and len(f.evidence) == 1
                   and f.evidence[0].field == f.fix.field
                   and f.evidence[0].bib_value == f.fix.bib_value
                   and f.evidence[0].ref_value == f.fix.ref_value)
            table = ""
            if rows and not dup:
                table = ('<table class="ev"><tr><th>Field</th><th>In your bib</th>'
                         '<th>Database record</th><th>Source</th><th>Verify</th></tr>' + "".join(rows) + "</table>")
            fix_html = ""
            if f.fix:
                link = (f'<a href="{_esc(f.fix.url)}" target="_blank" rel="noopener">verify ↗</a>'
                        if f.fix.url else "")
                if f.suggestion:
                    note = f'<div class="note">💡 {_esc(f.suggestion)}</div>'
                else:
                    note = f'<div class="note">Source: {_esc(f.fix.source)}</div>'
                fix_html = (
                    f'<div class="fix"><span class="flabel">🔧 Suggested fix</span>'
                    f'<span class="fname">{_esc(f.fix.field)}:</span>'
                    f'<code class="old">{_esc(f.fix.bib_value)}</code>'
                    f'<span class="arrow">→</span>'
                    f'<code class="new">{_esc(f.fix.ref_value)}</code> {link}'
                    f'{note}</div>')
            elif f.suggestion:
                fix_html = f'<div class="sug">💡 {_esc(f.suggestion)}</div>'
            findings.append(
                f'<div class="finding"><div class="fmsg">{_SEV_LABEL[f.severity]} '
                f'<code>[{_esc(f.code)}]</code> {_esc(f.message)}</div>{table}{fix_html}</div>')

        prov = f'Checked by: {", ".join(r.checked_by) or "—"}'
        bibmeta = _bibmeta(r, e) if e else ""
        head = (f'<div class="chead"><span class="key">{_esc(r.key)}</span>'
                f'<span class="badge">{_esc(r.entry_type)}</span>'
                f'<span class="sevchip">{_SEV_LABEL[r.severity]}</span></div>'
                f'<div class="title">{title}</div>')
        body = (f'<div class="bibmeta">{bibmeta}</div>'
                f'<div class="prov">{_esc(prov)}</div>' + "".join(findings))
        if r.severity == Severity.OK:
            cards.append(f'<details class="card sev-ok" data-sev="{sev}" data-key="{_esc(r.key.lower())}" '
                         f'data-title="{_esc(r.title.lower())}"><summary>{head}</summary>{body}</details>')
        else:
            cards.append(f'<section class="card sev-{sev}" data-sev="{sev}" data-key="{_esc(r.key.lower())}" '
                         f'data-title="{_esc(r.title.lower())}">{head}{body}</section>')

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>refright report — {_esc(Path(bib_path).name)}</title>
<style>{CSS}</style></head><body>
<header>
  <h1>refright bibliography report</h1>
  <div class="meta">File: {_esc(bib_path)} · Generated: {ts} · refright v{__version__} · Sources: Crossref / OpenAlex / arXiv / DBLP</div>
  {f'<div class="meta">🔎 Citation filter: {_esc(tex_note)}</div>' if tex_note else ''}
  <div class="chips">
    <span class="chip on" data-sev="all" onclick="setSev('all',this)">All {len(results)}</span>
    <span class="chip" data-sev="error" onclick="setSev('error',this)">❌ {counts[Severity.ERROR]}</span>
    <span class="chip" data-sev="warning" onclick="setSev('warning',this)">⚠️ {counts[Severity.WARNING]}</span>
    <span class="chip" data-sev="info" onclick="setSev('info',this)">ℹ️ {counts[Severity.INFO]}</span>
    <span class="chip" data-sev="ok" onclick="setSev('ok',this)">✅ {counts[Severity.OK]}</span>
  </div>
  <input id="q" placeholder="Search by bib key or title…">
</header>
<main>{''.join(cards)}</main>
<footer>In evidence tables, <span style="color:#991b1b">red = your bib value</span>, <span style="color:#065f46">green = database record</span> · every conclusion carries a "verify ↗" link to the publisher/database page for manual review · generated by refright</footer>
<script>{JS}</script>
</body></html>"""
