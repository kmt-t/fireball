#!/bin/bash
# shellcheck shell=bash
#
# Fireball TLA+ verification name resolution.
# Component ids are accepted as arguments and the runner resolves model/config/
# report filenames by normalized basename matching.

readonly VERIFY_COMPONENT_IDS=(
  eventdriven_coos
  ipc_deadlock
  loader_rollback
  vmmio
)

verify_resolve_component_id() {
  local normalized
  normalized="$(verify_normalize_name "$1")"

  case "$normalized" in
    coos|eventdrivencoos)
      printf '%s\n' eventdriven_coos
      ;;
    ipcdeadlock)
      printf '%s\n' ipc_deadlock
      ;;
    loaderrollback)
      printf '%s\n' loader_rollback
      ;;
    vmmio)
      printf '%s\n' vmmio
      ;;
    *)
      return 1
      ;;
  esac
}

verify_normalize_name() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '_-'
}

verify_find_artifact() {
  local root_dir="$1"
  local extension="$2"
  local component_id="$3"
  local needle path base normalized best_path best_score score

  needle="$(verify_normalize_name "$component_id")"
  best_path=""
  best_score=999999

  shopt -s nullglob
  for path in "$REPO_ROOT/$root_dir"/*"$extension"; do
    base="$(basename "$path" "$extension")"
    normalized="$(verify_normalize_name "$base")"

    if [[ "$normalized" == "$needle" ]]; then
      printf '%s\n' "${path#"$REPO_ROOT/"}"
      shopt -u nullglob
      return 0
    fi

    if [[ "$normalized" == *"$needle"* ]]; then
      score=$(( ${#normalized} - ${#needle} ))
      if [[ -z "$best_path" || $score -lt $best_score ]]; then
        best_path="${path#"$REPO_ROOT/"}"
        best_score=$score
      fi
    fi
  done
  shopt -u nullglob

  if [[ -n "$best_path" ]]; then
    printf '%s\n' "$best_path"
    return 0
  fi

  return 1
}
