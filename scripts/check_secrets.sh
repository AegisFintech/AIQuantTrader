#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--allow-in-tests]\n' "${0##*/}" >&2
}

allow_in_tests=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-in-tests)
      allow_in_tests=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
  shift
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  printf 'error: scripts/check_secrets.sh must run inside a git worktree\n' >&2
  exit 2
fi

cd "$repo_root"

mask_secret() {
  local value="$1"
  local length="${#value}"

  if [[ "$length" -le 8 ]]; then
    printf '****'
    return
  fi

  printf '%s…%s' "${value:0:4}" "${value: -4}"
}

should_skip_file() {
  local path="$1"

  if [[ "$allow_in_tests" == true && "$path" == tests/* ]]; then
    return 0
  fi

  return 1
}

is_hex_scan_file() {
  local path="$1"

  case "$path" in
    *.py|*.sh|*.md|*.json)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

findings=0
info_findings=0
t3n_api_var="T3N_API_KEY"
t3n_did_var="T3N_DID"
hex_regex='(^|[^0-9A-Fa-f])([0-9A-Fa-f]{64})([^0-9A-Fa-f]|$)'
common_regex='(^|[^[:alnum:]_])((sk_live_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{20,}|xoxb-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}))([^[:alnum:]_-]|$)'

report_finding() {
  local severity="$1"
  local path="$2"
  local line_no="$3"
  local pattern="$4"
  local secret="$5"

  printf '%s:%s: %s %s %s\n' \
    "$path" \
    "$line_no" \
    "$severity" \
    "$pattern" \
    "$(mask_secret "$secret")"

  if [[ "$severity" == "FAIL" ]]; then
    findings=1
  else
    info_findings=1
  fi
}

parse_match() {
  local kind="$1"
  local match="$2"
  local path
  local rest
  local line_no
  local line
  local value

  if [[ -z "$match" ]]; then
    return
  fi

  path="${match%%:*}"
  rest="${match#*:}"
  line_no="${rest%%:*}"
  line="${rest#*:}"

  case "$kind" in
    t3n_api)
      if [[ "$line" =~ ${t3n_api_var}=([^[:space:]#]+) ]]; then
        value="${BASH_REMATCH[1]}"
        if [[ -n "$value" && "$value" != \<* ]]; then
          report_finding "FAIL" "$path" "$line_no" "$t3n_api_var" "$value"
        fi
      fi
      ;;
    t3n_did)
      if [[ "$line" =~ ${t3n_did_var}=([^[:space:]#]+) ]]; then
        value="${BASH_REMATCH[1]}"
        if [[ -n "$value" && "$value" != did:* && "$value" != \<* ]]; then
          report_finding "INFO" "$path" "$line_no" "$t3n_did_var" "$value"
        fi
      fi
      ;;
    hex64)
      if [[ "$line" =~ $hex_regex ]]; then
        report_finding "FAIL" "$path" "$line_no" "hex64" "${BASH_REMATCH[2]}"
      fi
      ;;
    common)
      if [[ "$line" =~ $common_regex ]]; then
        report_finding "FAIL" "$path" "$line_no" "api-key-prefix" "${BASH_REMATCH[2]}"
      fi
      ;;
    *)
      printf 'error: unknown scan kind: %s\n' "$kind" >&2
      exit 2
      ;;
  esac
}

scan_git_grep() {
  local kind="$1"
  local regex="$2"
  shift 2

  if [[ "$#" -eq 0 ]]; then
    return
  fi

  local tmp
  local grep_status
  tmp="$(mktemp)"

  if git grep -IInE "$regex" -- "$@" > "$tmp"; then
    grep_status=0
  else
    grep_status=$?
  fi

  if [[ "$grep_status" -gt 1 ]]; then
    rm -f "$tmp"
    printf 'error: git grep failed while scanning %s\n' "$kind" >&2
    exit 2
  fi

  while IFS= read -r match || [[ -n "$match" ]]; do
    parse_match "$kind" "$match"
  done < "$tmp"

  rm -f "$tmp"
}

tracked_files=()
hex_files=()

while IFS= read -r -d '' path; do
  if should_skip_file "$path"; then
    continue
  fi

  tracked_files+=("$path")
  if is_hex_scan_file "$path"; then
    hex_files+=("$path")
  fi
done < <(git ls-files -z)

scan_git_grep "t3n_api" "${t3n_api_var}=[^[:space:]#]+" "${tracked_files[@]}"
scan_git_grep "t3n_did" "${t3n_did_var}=[^[:space:]#]+" "${tracked_files[@]}"
scan_git_grep "hex64" "$hex_regex" "${hex_files[@]}"
scan_git_grep "common" "$common_regex" "${tracked_files[@]}"

if [[ "$findings" -ne 0 ]]; then
  printf 'Secrets scan failed: hardcoded secret-like values found.\n' >&2
  exit 1
fi

if [[ "$info_findings" -ne 0 ]]; then
  printf 'Secrets scan clean: no blocking findings. Informational notices are listed above.\n'
else
  printf 'Secrets scan clean: no hardcoded secret-like values found.\n'
fi
