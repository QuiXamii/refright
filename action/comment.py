#!/usr/bin/env python3
"""Build the Markdown PR-comment / step-summary body from the aggregate JSON."""
import json
import sys

agg = json.load(open(sys.argv[1]))

lines = ["## refright — reference check", ""]
total_err = sum(f["counts"]["ERROR"] for f in agg)
total_warn = sum(f["counts"]["WARNING"] for f in agg)
n_entries = sum(sum(f["counts"].values()) for f in agg)

if total_err == 0:
    lines.append(f"✅ **{n_entries} references checked, no errors.** "
                 f"({total_warn} warnings)")
else:
    lines.append(f"❌ **{total_err} error(s)** across {n_entries} references "
                 f"({total_warn} warnings).")
lines.append("")
lines.append("| file | ✅ | ℹ️ | ⚠️ | ❌ |")
lines.append("| --- | ---: | ---: | ---: | ---: |")
for f in agg:
    c = f["counts"]
    lines.append(f"| `{f['file']}` | {c['OK']} | {c['INFO']} | {c['WARNING']} | {c['ERROR']} |")

details = [(f["file"], e) for f in agg for e in f["errors"]]
if details:
    lines += ["", "<details><summary>Error details</summary>", ""]
    for path, e in details:
        lines.append(f"- **{e['key']}** (`{path}`) — `{e['code']}`: {e['message']}")
    lines += ["", "</details>"]

lines += ["", "---",
          "*Each finding comes with field-level evidence and one-click verification "
          "links — run refright locally with `--html` for the full report.*"]
print("\n".join(lines))
