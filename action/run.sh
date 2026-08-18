#!/usr/bin/env bash
# refright composite-action driver. Inputs arrive as REFRIGHT_* env vars.
# Portable to bash 3.2 (macOS runners): no globstar, guarded array expansion.
set -u
shopt -s nullglob

files=()
for pat in ${REFRIGHT_FILES:-**/*.bib}; do
    case "$pat" in
        **/*) # recursive glob -> find on the basename pattern
            base="${pat##**/}"
            while IFS= read -r f; do files+=("$f"); done < <(
                find . -type f -name "$base" -not -path './.git/*' | sed 's|^\./||')
            ;;
        *)
            for f in $pat; do [ -f "$f" ] && files+=("$f"); done
            ;;
    esac
done
if [ ${#files[@]} -eq 0 ]; then
    echo "refright: no files matched '${REFRIGHT_FILES}' — nothing to do"
    exit 0
fi

tex_args=()
for t in ${REFRIGHT_TEX:-}; do tex_args+=(--tex "$t"); done

tmp=$(mktemp -d)
worst=0   # 1 once any file reports an ERROR
summary_json="$tmp/summary.json"
echo "[]" > "$summary_json"

for f in "${files[@]}"; do
    out="$tmp/$(echo "$f" | tr '/ ' '__').json"
    echo "::group::refright $f"
    python3 -m refright "$f" --json "$out" --workers "${REFRIGHT_WORKERS:-6}" ${tex_args[@]+"${tex_args[@]}"}
    rc=$?
    echo "::endgroup::"
    [ $rc -eq 1 ] && worst=1
    python3 - "$out" "$f" "$summary_json" <<'PY'
import json, sys
res_path, name, agg_path = sys.argv[1:4]
try:
    data = json.load(open(res_path))
except OSError:
    data = []
counts = {"OK": 0, "INFO": 0, "WARNING": 0, "ERROR": 0}
for r in data:
    counts[r["severity"]] += 1
errors = [{"key": r["key"], "code": f["code"], "message": f["message"]}
          for r in data for f in r["findings"] if f["severity"] == "ERROR"]
agg = json.load(open(agg_path))
agg.append({"file": name, "counts": counts, "errors": errors})
json.dump(agg, open(agg_path, "w"), ensure_ascii=False)
PY
done

# step summary (always) + PR comment (optional)
python3 "${REFRIGHT_ACTION_PATH}/action/comment.py" "$summary_json" > "$tmp/comment.md"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    cat "$tmp/comment.md" >> "$GITHUB_STEP_SUMMARY"
fi

if [ "${REFRIGHT_COMMENT:-true}" = "true" ] && [ -n "${REFRIGHT_PR:-}" ] \
   && [ -n "${REFRIGHT_TOKEN:-}" ] && [ -n "${REFRIGHT_REPO:-}" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' \
        -X POST "https://api.github.com/repos/${REFRIGHT_REPO}/issues/${REFRIGHT_PR}/comments" \
        -H "Authorization: Bearer ${REFRIGHT_TOKEN}" \
        -H 'Accept: application/vnd.github+json' \
        --data-binary @<(python3 -c 'import json,sys; print(json.dumps({"body": open(sys.argv[1]).read()}))' "$tmp/comment.md"))
    echo "refright: PR comment HTTP $code"
fi

if [ "${REFRIGHT_FAIL_ON_ERROR:-true}" = "true" ] && [ $worst -eq 1 ]; then
    echo "refright: ERROR-level findings present — failing the check"
    exit 1
fi
exit 0
