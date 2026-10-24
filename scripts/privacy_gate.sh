#!/usr/bin/env bash
# Generic publishability checks. Personal/org terms belong in a local file (gitignored).
# Compatible with bash 3.2+ (no mapfile).
set -euo pipefail
set -o pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v rg >/dev/null || {
  echo "privacy_gate: ripgrep (rg) is required" >&2
  exit 2
}

ARG_TERMS_FILE="${1:-}"
LOCAL_TERMS_FILE="${FERNRUF_PRIVACY_TERMS_FILE:-}"
if [[ -z "$LOCAL_TERMS_FILE" && -f "$ROOT/.privacy-terms.local" ]]; then
  LOCAL_TERMS_FILE="$ROOT/.privacy-terms.local"
fi

for req in "$ARG_TERMS_FILE" "$LOCAL_TERMS_FILE"; do
  if [[ -n "$req" && ! -f "$req" ]]; then
    echo "privacy_gate: terms file not found: $req" >&2
    exit 2
  fi
done

# No third-party product names here; use FERNRUF_PRIVACY_TERMS_FILE / .privacy-terms.local.
BRAND_TERMS=()
FUNDING_TERMS=(
  ko-fi buymeacoffee "buy me a coffee" github.com/sponsors patreon liberapay opencollective paypal.me
)

RG_GLOBS=(
  --glob '!.git/**'
  --glob '!uv.lock'
  --glob '!.venv/**'
  --glob '!verify-evidence/**'
  --glob '!scripts/privacy_gate.sh'
  --glob '!.privacy-terms.local'
)
PHONE_SKIP_GLOBS=(
  --glob '!.git/**'
  --glob '!uv.lock'
  --glob '!.venv/**'
  --glob '!verify-evidence/**'
  --glob '!scripts/privacy_gate.sh'
  --glob '!.privacy-terms.local'
  --glob '!**/*.png'
  --glob '!**/*.jpg'
  --glob '!**/*.jpeg'
  --glob '!**/*.gif'
  --glob '!**/*.webp'
  --glob '!**/*.ico'
  --glob '!**/*.wav'
  --glob '!**/*.mp3'
  --glob '!**/*.pyc'
  --glob '!**/__pycache__/**'
)
FAIL=0
GATE_HAS_GIT=0
GATE_ISSUES=0

gate_rel_path() {
  local p="$1"
  p="${p#"$ROOT"/}"
  p="${p#./}"
  printf '%s' "$p"
}

gate_fail() {
  local msg="$1"
  local first="${msg%%:*}"
  if [[ "$first" == "$ROOT"/* ]]; then
    msg="$(gate_rel_path "$first")${msg#"$first"}"
  fi
  echo "FAIL: $msg" >&2
  FAIL=1
  GATE_ISSUES=$((GATE_ISSUES + 1))
}

RG_ERROR_REPORTED=0
gate_rg_error_once() {
  local msg="$1"
  if [[ "$RG_ERROR_REPORTED" -eq 0 ]]; then
    gate_fail "$msg"
    RG_ERROR_REPORTED=1
  fi
  FAIL=1
}

gate_env_excluded() {
  local f="$1"
  case "$f" in
    .env) return 0 ;;
    .env.local) return 0 ;;
    .env.*.local) return 0 ;;
    .env.example) return 1 ;;
    .env.*)
      if [[ "$GATE_HAS_GIT" -eq 1 ]] && git -C "$ROOT" ls-files --error-unmatch "$f" >/dev/null 2>&1; then
        return 1
      fi
      return 0
      ;;
  esac
  return 1
}

gate_path_excluded() {
  local f="$1"
  case "$f" in
    uv.lock|verify-evidence/*|scripts/privacy_gate.sh|.privacy-terms.local) return 0 ;;
    *.png|*.jpg|*.jpeg|*.gif|*.webp|*.ico|*.wav|*.mp3|*.pyc) return 0 ;;
    data/*|var/*|logs/*|log/*|transcripts/*|transcript/*) return 0 ;;
    *.log|*.jsonl|*.sqlite|*.sqlite3) return 0 ;;
  esac
  gate_env_excluded "$f" && return 0
  return 1
}

gate_build_file_list() {
  if [[ "$GATE_HAS_GIT" -eq 0 ]]; then
    find "$ROOT" -type f ! -path '*/.git/*' 2>/dev/null | while IFS= read -r f; do
      f="${f#"$ROOT"/}"
      f="${f#/}"
      gate_path_excluded "$f" && continue
      printf '%s\n' "$f"
    done
    return
  fi
  {
    git -C "$ROOT" ls-files
    git -C "$ROOT" ls-files --others --exclude-standard
  } | sort -u | while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    gate_path_excluded "$f" && continue
    printf '%s\n' "$f"
  done
}

ip_is_documentation_range() {
  local ip="$1"
  [[ "$ip" =~ ^127\. ]] && return 0
  [[ "$ip" =~ ^192\.0\.2\. ]] && return 0
  [[ "$ip" =~ ^198\.51\.100\. ]] && return 0
  [[ "$ip" =~ ^203\.0\.113\. ]] && return 0
  return 1
}

load_terms() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  grep -vE '^\s*(#|$)' "$path" || true
}

LOCAL_TERMS=()
while IFS= read -r _line || [[ -n "${_line:-}" ]]; do
  [[ -z "${_line:-}" ]] && continue
  LOCAL_TERMS+=("$_line")
done <<EOF
$(load_terms "$LOCAL_TERMS_FILE")
EOF
ARG_TERMS=()
while IFS= read -r _line || [[ -n "${_line:-}" ]]; do
  [[ -z "${_line:-}" ]] && continue
  ARG_TERMS+=("$_line")
done <<EOF
$(load_terms "$ARG_TERMS_FILE")
EOF

rg_collect() {
  local pattern="$1"
  shift
  local out rc
  set +e
  out=$(rg -n "$pattern" "$@" 2>&1)
  rc=$?
  set -e
  if [[ "$rc" -eq 2 ]]; then
    echo "FAIL: rg error ($pattern): $out" >&2
    return 2
  fi
  if [[ "$rc" -eq 0 ]]; then
    printf '%s\n' "$out"
  fi
  return 0
}

scan_term() {
  local term="$1"
  [[ -z "$term" ]] && return 0
  local out rc f path line file line_no rest
  while IFS= read -r f || [[ -n "${f:-}" ]]; do
    [[ -z "${f:-}" ]] && continue
    gate_path_excluded "$f" && continue
    path="$ROOT/$f"
    [[ -f "$path" ]] || continue
    set +e
    out=$(rg -i --with-filename -n --no-heading "$term" "$path" 2>&1)
    rc=$?
    set -e
    if [[ "$rc" -eq 2 ]]; then
      gate_rg_error_once "${f}:banned_term (rg error)"
      return
    fi
    if [[ "$rc" -ne 0 ]]; then
      continue
    fi
    while IFS= read -r line || [[ -n "${line:-}" ]]; do
      [[ -z "${line:-}" ]] && continue
      file="${line%%:*}"
      rest="${line#*:}"
      line_no="${rest%%:*}"
      gate_fail "${file}:${line_no}:banned_term"
    done <<EOF
$out
EOF
  done < "$_gate_files"
}

phone_digits_only() {
  local raw="$1"
  local out=""
  local c
  local i
  for ((i = 0; i < ${#raw}; i++)); do
    c="${raw:i:1}"
    if [[ "$c" == [0-9] ]]; then
      out="${out}${c}"
    fi
  done
  printf '%s' "$out"
}

# Fake/example numbers that appear in this repo (docs, tests, verify fixtures).
ALLOWED_PHONE_E164=(
  +349009911223
  +12025550100
  +491180000000
  +491370000000
  +491713920011
  +491713920000
  +491713920001
  +491713920002
  +491800000000
  +4930231251
  +49302312512
  +493023125123
  +493023125124
  +497000000000
  +499001234567
  +499009999999
  +499999999999
)

phone_is_all_same_digit() {
  local digits="$1"
  [[ -z "$digits" ]] && return 1
  [[ "$digits" =~ ^([0-9])\1+$ ]]
}

phone_is_strict_sequential() {
  local digits="$1"
  if [[ "$digits" == "0123456789" || "$digits" == "123456789" || "$digits" == "1234567890" ]]; then
    return 0
  fi
  return 1
}

phone_e164_allowed_exact() {
  local e164="$1"
  local n
  for n in ${ALLOWED_PHONE_E164[@]+"${ALLOWED_PHONE_E164[@]}"}; do
    if [[ "$e164" == "$n" ]]; then
      return 0
    fi
  done
  return 1
}

phone_compact_raw() {
  local raw="$1"
  local out="$raw"
  out="${out//[[:space:]-]/}"
  out="${out//\//}"
  out="${out//./}"
  out="${out//(\(/}"
  out="${out//(\)/}"
  while [[ "$out" == *"+490"* ]]; do
    out="${out//+490/+49}"
  done
  printf '%s' "$out"
}

phone_is_allowed_example() {
  local raw="$1"
  local compact
  compact=$(phone_compact_raw "$raw")
  local e164=""
  local digits=""

  if [[ "${compact:0:1}" == "+" ]]; then
    digits=$(phone_digits_only "$compact")
    e164="+${digits}"
  else
    digits=$(phone_digits_only "$compact")
    if [[ "${digits:0:2}" == "00" ]]; then
      e164="+${digits:2}"
    elif [[ "${digits:0:1}" == "0" ]]; then
      e164="+49${digits:1}"
    else
      e164="+${digits}"
    fi
  fi

  [[ -z "$digits" ]] && return 1

  if phone_is_all_same_digit "$digits"; then
    return 0
  fi
  if phone_is_strict_sequential "$digits"; then
    return 0
  fi
  if phone_e164_allowed_exact "$e164"; then
    return 0
  fi
  return 1
}

phone_hit_min_digits() {
  local hit="$1"
  local d
  d=$(phone_digits_only "$hit")
  [[ ${#d} -ge 8 ]]
}

phone_skip_dial_prefix_of_allowed() {
  local line="$1"
  local hit="$2"
  local n ed hd
  hd=$(phone_digits_only "$hit")
  for n in ${ALLOWED_PHONE_E164[@]+"${ALLOWED_PHONE_E164[@]}"}; do
    ed=$(phone_digits_only "$n")
    if [[ "$line" != *"$n"* ]]; then
      continue
    fi
    if [[ "$hd" == "00${ed}" || "$hd" == "0${ed:2}" ]]; then
      return 0
    fi
    if [[ "$hd" == "0${ed}" || "$hd" == "00${ed}" ]]; then
      return 0
    fi
    if [[ "$line" == *"00${ed}"* && "$hd" == "0${ed:1}" ]]; then
      return 0
    fi
  done
  return 1
}

phone_skip_false_positive() {
  local line="$1"
  local hit="$2"
  local d dotted_digits
  d=$(phone_digits_only "$hit")
  if phone_skip_dial_prefix_of_allowed "$line" "$hit"; then
    return 0
  fi
  if [[ ${#d} -lt 8 ]]; then
    return 0
  fi
  if echo "$hit" | rg -q '^[0-9]+(\.[0-9]+){1,3}$'; then
    dotted_digits=$(phone_digits_only "$hit")
    if [[ "${hit:0:1}" == "0" && ${#dotted_digits} -ge 9 ]]; then
      return 1
    fi
    return 0
  fi
  if echo "$line" | rg -q '(--at-iso|T[0-9]{2}:[0-9]{2})' && echo "$hit" | rg -q '[0-9]{2,4}[/-][0-9]{2}'; then
    return 0
  fi
  if echo "$hit" | rg -q '^[0-9]{1,3}(\.[0-9]{1,3}){2,3}$'; then
    dotted_digits=$(phone_digits_only "$hit")
    if [[ "${hit:0:1}" == "0" && ${#dotted_digits} -ge 9 ]]; then
      return 1
    fi
    return 0
  fi
  if echo "$line" | rg -q '[0-9a-f]{7,40}' && [[ "$line" == *"$hit"* ]]; then
    if echo "$line" | rg -q "[0-9a-f]{40}"; then
      return 0
    fi
  fi
  return 1
}

phone_skip_embedded_national() {
  local line="$1"
  local hit="$2"
  local digits e164 ed
  digits=$(phone_digits_only "$hit")
  if [[ "${digits:0:1}" != "0" ]]; then
    return 1
  fi
  while IFS= read -r e164; do
    [[ -z "$e164" ]] && continue
    ed=$(phone_digits_only "$e164")
    if [[ "$ed" == *"$digits"* && "$digits" != "$ed" ]]; then
      return 0
    fi
  done < <(echo "$line" | rg -o '\+[1-9][0-9]{6,14}' 2>/dev/null || true)
  return 1
}

phone_hits_on_line() {
  local line="$1"
  local hit
  while IFS= read -r hit; do
    [[ -z "$hit" ]] && continue
    if phone_skip_false_positive "$line" "$hit"; then
      continue
    fi
    if phone_skip_embedded_national "$line" "$hit"; then
      continue
    fi
    printf '%s\n' "$hit"
  done < <(
    {
      echo "$line" | rg -o '\+[1-9][0-9]{6,14}' 2>/dev/null || true
      echo "$line" | rg -o '\+[1-9][0-9]{0,2}([[:space:]./()-][0-9]{2,}){1,6}' 2>/dev/null || true
      echo "$line" | rg -o '\+49[[:space:]]*\(0\)[[:space:]/0-9.()-]{6,}' 2>/dev/null || true
      echo "$line" | rg -o '0[1-9][0-9]{8,11}' 2>/dev/null || true
      echo "$line" | rg -o '0[1-9][0-9]{1,4}([[:space:]./-][0-9]{2,8}){1,4}' 2>/dev/null || true
      echo "$line" | rg -o '0[1-9][0-9]{3,4}[[:space:]./-][0-9]{4,9}' 2>/dev/null || true
      echo "$line" | rg -o '\(0[1-9][0-9]{1,4}\)[[:space:]]*[0-9]{4,}' 2>/dev/null || true
      echo "$line" | rg -o '0[1-9][0-9]{1,4}\.[0-9]{2,4}\.[0-9]{4,}' 2>/dev/null || true
      echo "$line" | rg -o '0[1-9][0-9]{4}\.[0-9]{4,}' 2>/dev/null || true
    } | sort -u
  )
}

scan_with_file_list() {
  local pattern="$1"
  local category="${2:-match}"
  local f path out rc line file_line
  while IFS= read -r f || [[ -n "${f:-}" ]]; do
    [[ -z "${f:-}" ]] && continue
    path="$ROOT/$f"
    [[ -f "$path" ]] || continue
    set +e
    out=$(rg --with-filename -n --no-heading -e "$pattern" "$path" 2>&1)
    rc=$?
    set -e
    if [[ "$rc" -eq 2 ]]; then
      gate_rg_error_once "${f}:${category} (rg error)"
      return 2
    fi
    if [[ "$rc" -ne 0 ]]; then
      continue
    fi
    while IFS= read -r line || [[ -n "${line:-}" ]]; do
      [[ -z "${line:-}" ]] && continue
      printf '%s\n' "$line"
    done <<EOF
$out
EOF
  done < "$_gate_files"
  return 0
}

scan_phone_hits() {
  scan_with_file_list "$1" "phone"
}

scan_private_ips() {
  scan_with_file_list "$1" "private_ip"
}

_gate_files=$(mktemp)
_tmp192=$(mktemp)
_tmp172=$(mktemp)
_tmp10=$(mktemp)
_tmpphone=$(mktemp)
_seen_phone=$(mktemp)
trap 'rm -f "$_gate_files" "$_tmp192" "$_tmp172" "$_tmp10" "$_tmpphone" "$_seen_phone"' EXIT
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GATE_HAS_GIT=1
fi
gate_build_file_list >"$_gate_files"

for req in README.md PRIVACY.md SECURITY.md CONTRIBUTING.md; do
  if [[ ! -f "$ROOT/$req" ]]; then
    echo "FAIL: required file missing: $req" >&2
    FAIL=1
  fi
done

if [[ -f "$ROOT/.github/FUNDING.yml" ]]; then
  echo "FAIL: .github/FUNDING.yml must not exist" >&2
  FAIL=1
fi

for term in ${BRAND_TERMS[@]+"${BRAND_TERMS[@]}"} ${FUNDING_TERMS[@]+"${FUNDING_TERMS[@]}"} ${LOCAL_TERMS[@]+"${LOCAL_TERMS[@]}"} ${ARG_TERMS[@]+"${ARG_TERMS[@]}"}; do
  scan_term "$term"
done

if ! scan_private_ips '\b192\.168\.\d{1,3}\.\d{1,3}\b' >"$_tmp192"; then
  FAIL=1
fi
if ! scan_private_ips '\b172\.(1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}\b' >"$_tmp172"; then
  FAIL=1
fi
if ! scan_private_ips '\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b' >"$_tmp10"; then
  FAIL=1
fi

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  ip=$(echo "$line" | rg -o '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}' | head -1 || true)
  [[ -z "$ip" ]] && continue
  if ip_is_documentation_range "$ip"; then
    continue
  fi
  file="${line%%:*}"
  rest="${line#*:}"
  line_no="${rest%%:*}"
  gate_fail "${file}:${line_no}:private_ip"
done < <(cat "$_tmp192" "$_tmp172" "$_tmp10")

{
  scan_phone_hits '\+[1-9][0-9]{6,14}' || true
  scan_phone_hits '\+[1-9][0-9]{0,2}[[:space:]./()-][0-9]{2,}([[:space:]./()-][0-9]{2,}){1,5}' || true
  scan_phone_hits '\+49[[:space:]]*\(0\)[[:space:]/0-9.()-]{6,}' || true
  scan_phone_hits '0[1-9][0-9]{8,11}' || true
  scan_phone_hits '0[1-9][0-9]{1,4}[[:space:]./-][0-9]{3,8}([[:space:]./-][0-9]{2,8})?' || true
  scan_phone_hits '0[1-9][0-9]{3,4}[[:space:]./-][0-9]{4,9}' || true
  scan_phone_hits '\(0[1-9][0-9]{1,4}\)[[:space:]]*[0-9]{4,}' || true
  scan_phone_hits '0[1-9][0-9]{1,4}\.[0-9]{2,4}\.[0-9]{4,}' || true
  scan_phone_hits '0[1-9][0-9]{4}\.[0-9]{4,}' || true
} >"$_tmpphone" || true

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  while IFS= read -r hit; do
    [[ -z "$hit" ]] && continue
    if ! phone_hit_min_digits "$hit"; then
      continue
    fi
    if phone_skip_false_positive "$line" "$hit"; then
      continue
    fi
    if phone_skip_embedded_national "$line" "$hit"; then
      continue
    fi
    if phone_is_allowed_example "$hit"; then
      continue
    fi
    file="${line%%:*}"
    rest="${line#*:}"
    line_no="${rest%%:*}"
    rel="$(gate_rel_path "$file")"
    key="${rel}:${line_no}"
    if grep -qxF "$key" "$_seen_phone" 2>/dev/null; then
      continue
    fi
    echo "$key" >> "$_seen_phone"
    gate_fail "${key}:phone"
  done < <(phone_hits_on_line "$line")
done < "$_tmpphone"

if [[ "$FAIL" -ne 0 ]]; then
  echo "privacy_gate=fail (${GATE_ISSUES} issues)" >&2
  exit 1
fi
echo "privacy_gate=pass (brands=${#BRAND_TERMS[@]} funding=${#FUNDING_TERMS[@]} local=${#LOCAL_TERMS[@]} arg=${#ARG_TERMS[@]})"
