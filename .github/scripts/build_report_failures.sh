#!/bin/bash
# build_report_failures.sh — turn the per-app failure records the engine left in
# temp/failures into ONE batched Telegram message on the failure-notification
# topic, uploading each build log to xi.pe and linking it inline.
#
# Why a separate CI step instead of notifying from the engine: the engine stays
# free of network/notification code (and of Telegram credentials in its env);
# this step owns the whole report and is fail-soft like every other notify path,
# so one bad xi.pe upload never fails the build step.
#
# Records (written by scripts/utils.sh + scripts/build.sh):
#   <slug>.json    a build that aborted (has <slug>.log alongside)
#   <slug>_dl.json a build whose download sources were all exhausted
#
# Route B: when this step actually sends, it writes reported_failures=true so
# ci.yml can skip the generic "🔴 CI #N failed" alert for the same run.
#
# Env: TG_TOKEN TG_CHAT_ID TG_THREAD_NOTIFY APKS_REPO
#      GITHUB_SERVER_URL GITHUB_REPOSITORY GITHUB_RUN_ID GITHUB_OUTPUT
set -euo pipefail

NL=$'\n'
FAILURES_DIR="temp/failures"
TG_LIMIT=4096
XIPE_URL_BASE="https://xi.pe"

if [ -z "${TG_TOKEN:-}" ]; then
	echo "TG_TOKEN is not set. Skipping failure report."
	exit 0
fi
if [ ! -d "$FAILURES_DIR" ] || ! find "$FAILURES_DIR" -maxdepth 1 -name '*.json' -print -quit | grep -q .; then
	echo "No failure records in $FAILURES_DIR; nothing to report."
	exit 0
fi

# Markdown (legacy) has no escape-all helper; strip the few characters that
# would corrupt a [label](url) or a *bold* run in app-authored text.
md_clean() { printf '%s' "$1" | tr -d '[]`'; }

version_label() { # $1=version — hide the unhelpful placeholder values
	local v="${1:-}"
	case "$v" in
	'' | auto | latest | beta | exp) printf '' ;;
	*) printf 'v%s' "${v#v}" ;;
	esac
}

# ---- Collect build failures (upload each log first) ----
build_lines=""
build_count=0
shopt -s nullglob
for json in "$FAILURES_DIR"/*.json; do
	case "$json" in *_dl.json) continue ;; esac
	slug="$(basename "${json%.json}")"
	# A cascaded case (download exhausted, then build failed) is reported once,
	# as the build failure; drop its dl twin.
	[ -f "$FAILURES_DIR/${slug}_dl.json" ] && rm -f "$FAILURES_DIR/${slug}_dl.json"

	app="$(jq -r '.app // empty' "$json")"
	ver="$(version_label "$(jq -r '.version // empty' "$json")")"
	vc="$(jq -r '.vc // empty' "$json")"
	patches="$(jq -r '.patches_src // empty' "$json")"

	entry="📱 $(md_clean "$app")${ver:+ $ver}"
	[ -n "$vc" ] && entry+="${NL}  ╰ VC: $vc"
	# Upload the log; a non-URL response is treated as failure (no link, not a
	# dropped message) so the app is still listed.
	if [ -f "$FAILURES_DIR/$slug.log" ]; then
		link="$(curl -sS --data-binary "@$FAILURES_DIR/$slug.log" "$XIPE_URL_BASE/" 2> /dev/null || true)"
		case "$link" in
		"$XIPE_URL_BASE/"*) entry+="${NL}  ╰ [Log]($link)" ;;
		esac
	fi
	# patches_src is owner/repo; assume a GitHub host (the report is a pointer,
	# the log carries the real source host).
	[ -n "$patches" ] && entry+="${NL}  ╰ Patches: [$(md_clean "$patches")](https://github.com/$patches)"

	build_lines+="${entry}${NL}${NL}"
	build_count=$((build_count + 1))
done

# ---- Collect download failures (no log upload; the point is a manual upload) ----
dl_lines=""
dl_count=0
for json in "$FAILURES_DIR"/*_dl.json; do
	app="$(jq -r '.app // empty' "$json")"
	ver="$(version_label "$(jq -r '.version // empty' "$json")")"
	vc="$(jq -r '.vc // empty' "$json")"

	entry="📱 $(md_clean "$app")${ver:+ $ver}"
	[ -n "$vc" ] && entry+="${NL}  ╰ VC: $vc"
	dl_lines+="${entry}${NL}${NL}"
	dl_count=$((dl_count + 1))
done
shopt -u nullglob

if [ "$build_count" = 0 ] && [ "$dl_count" = 0 ]; then
	echo "Failure records present but none parsed; nothing to report."
	exit 0
fi

# ---- Assemble: section headers, actionable hints, run link ----
MSG=""
if [ "$build_count" -gt 0 ]; then
	MSG+="*🚨 Build Failures ($build_count app$([ "$build_count" = 1 ] && echo '' || echo 's'))*${NL}${NL}${build_lines}"
fi
if [ "$dl_count" -gt 0 ]; then
	[ -n "$MSG" ] && MSG+="${NL}"
	MSG+="*📡 Download Failures ($dl_count app$([ "$dl_count" = 1 ] && echo '' || echo 's'))*${NL}${NL}${dl_lines}"
fi

HINTS=""
[ "$build_count" -gt 0 ] && HINTS+="⚠️ _Build failures:_ report the issue to the patch author with the log link."
if [ "$dl_count" -gt 0 ]; then
	[ -n "$HINTS" ] && HINTS+="${NL}"
	HINTS+="📦 _Download failures:_ upload the APK to the [cache repo](https://github.com/${APKS_REPO:-nullcpy/apks}) manually."
fi
[ -n "$HINTS" ] && MSG+="${NL}${HINTS}"

if [ -n "${GITHUB_REPOSITORY:-}" ] && [ -n "${GITHUB_RUN_ID:-}" ]; then
	run_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
	MSG+="${NL}${NL}⚙️ [View Run]($run_url)"
fi

# ---- Chunk on line boundaries and send ----
send_chunk() {
	local text="${1:-}"
	[ -z "$text" ] && return 0
	curl -s -X POST \
		--data-urlencode "parse_mode=Markdown" \
		--data-urlencode "disable_web_page_preview=true" \
		--data-urlencode "text=${text}" \
		--data-urlencode "chat_id=${TG_CHAT_ID:-@rvb27}" \
		--data-urlencode "message_thread_id=${TG_THREAD_NOTIFY:-3031}" \
		"https://api.telegram.org/bot${TG_TOKEN}/sendMessage" > /dev/null || true
}

CHUNK=""
sent=0
while IFS= read -r LINE; do
	CANDIDATE="${CHUNK:+${CHUNK}${NL}}${LINE}"
	if [ "${#CANDIDATE}" -le "$TG_LIMIT" ]; then
		CHUNK="$CANDIDATE"
	else
		if [ -n "$CHUNK" ]; then
			send_chunk "$CHUNK"
			sent=1
		fi
		CHUNK="$LINE"
	fi
done <<< "$MSG"
if [ -n "$CHUNK" ]; then
	send_chunk "$CHUNK"
	sent=1
fi

if [ "$sent" = 1 ] && [ -n "${GITHUB_OUTPUT:-}" ]; then
	echo "reported_failures=true" >> "$GITHUB_OUTPUT"
fi
