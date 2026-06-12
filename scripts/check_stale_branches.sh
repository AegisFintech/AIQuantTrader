#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

usage() {
  printf 'Usage: %s [--delete --yes-i-mean-it]\n' "$0"
}

delete_requested=false
delete_confirmed=false

for arg in "$@"; do
  case "$arg" in
    --delete)
      delete_requested=true
      ;;
    --yes-i-mean-it)
      delete_confirmed=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 0
      ;;
  esac
done

if [[ "$delete_requested" == true && "$delete_confirmed" != true ]]; then
  printf 'Deletion mode requires: --delete --yes-i-mean-it\n' >&2
  exit 0
fi

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  printf 'Not inside a Git repository.\n' >&2
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if ! git rev-parse --verify --quiet main >/dev/null; then
  printf 'Branch main was not found.\n' >&2
  exit 0
fi

report_local_branch() {
  local branch="$1"
  local current_branch

  if [[ "$branch" == "main" ]]; then
    return
  fi

  current_branch="$(git branch --show-current)"
  if [[ -n "$current_branch" && "$branch" == "$current_branch" ]]; then
    return
  fi

  if git merge-base --is-ancestor "$branch" main; then
    printf '[local] %s\n' "$branch"

    if [[ "$delete_requested" == true ]]; then
      if git branch -d "$branch" >/dev/null; then
        printf '[deleted local] %s\n' "$branch"
      else
        printf '[warn] failed to delete local branch: %s\n' "$branch" >&2
      fi
    fi
  fi
}

report_remote_branch() {
  local ref="$1"
  local remote="${ref%%/*}"
  local branch="${ref#*/}"

  if [[ "$ref" != */* || "$ref" == */HEAD || "$branch" == "main" ]]; then
    return
  fi

  if git merge-base --is-ancestor "$ref" main; then
    printf '[remote] %s\n' "$ref"

    if [[ "$delete_requested" == true ]]; then
      if git push "$remote" --delete "$branch" >/dev/null; then
        printf '[deleted remote] %s\n' "$ref"
      else
        printf '[warn] failed to delete remote branch: %s\n' "$ref" >&2
      fi
    fi
  fi
}

while IFS= read -r branch; do
  [[ -n "$branch" ]] || continue
  report_local_branch "$branch"
done < <(git for-each-ref --format='%(refname:short)' refs/heads)

while IFS= read -r ref; do
  [[ -n "$ref" ]] || continue
  report_remote_branch "$ref"
done < <(git for-each-ref --format='%(refname:short)' refs/remotes)

exit 0
