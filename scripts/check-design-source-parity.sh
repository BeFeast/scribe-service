#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${SCRIBE_DESIGN_ARCHIVE:-/mnt/storage/src/Scribe.redesign.zip}"
EXPECTED_SHA="3253d4d262b00a25bdb07bf4ff3c7112998b9b8ee917211438aa220bcdd9719a"
REPO_EXPORT="${ROOT}/design/scribe-redesign-2026-05-24/app"
STAGED_SOURCE="${ROOT}/web/spa/src/design-source/app"

app_files=(
	"app.jsx"
	"command-palette.jsx"
	"data.jsx"
	"icons.jsx"
	"job-pages.jsx"
	"library.jsx"
	"ops.jsx"
	"settings.jsx"
	"shell.jsx"
	"styles.css"
	"transcript-detail.jsx"
	"tweaks-panel.jsx"
)

compare_file() {
	local expected_path="$1"
	local actual_path="$2"
	local label="$3"

	if ! cmp -s "${expected_path}" "${actual_path}"; then
		echo "Design source parity failed for ${label}" >&2
		diff -u "${expected_path}" "${actual_path}" | sed -n '1,120p' >&2 || true
		exit 1
	fi
}

for file in "${app_files[@]}"; do
	if [[ ! -f "${REPO_EXPORT}/${file}" ]]; then
		echo "Design source parity failed: missing repo export app/${file}" >&2
		exit 1
	fi
	if [[ ! -f "${STAGED_SOURCE}/${file}" ]]; then
		echo "Design source parity failed: missing staged source app/${file}" >&2
		exit 1
	fi
	compare_file "${REPO_EXPORT}/${file}" "${STAGED_SOURCE}/${file}" "app/${file}"
done

if [[ -f "${ARCHIVE}" ]]; then
	actual_sha="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
	if [[ "${actual_sha}" != "${EXPECTED_SHA}" ]]; then
		echo "Design archive checksum failed: expected ${EXPECTED_SHA}, got ${actual_sha}" >&2
		exit 1
	fi

	tmpdir="$(mktemp -d)"
	trap 'rm -rf "${tmpdir}"' EXIT
	for file in "${app_files[@]}"; do
		unzip -p "${ARCHIVE}" "app/${file}" > "${tmpdir}/${file}"
		compare_file "${tmpdir}/${file}" "${STAGED_SOURCE}/${file}" "archive app/${file}"
		compare_file "${tmpdir}/${file}" "${REPO_EXPORT}/${file}" "repo export app/${file}"
	done
else
	echo "Design archive not found at ${ARCHIVE}; verified repo export against staged source only." >&2
fi

echo "Design source parity check passed."
