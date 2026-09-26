#!/usr/bin/env bash
# install-githooks managed
set -uo pipefail

if ! command -v git >/dev/null 2>&1; then
  printf 'Git não encontrado.\n' >&2
  exit 1
fi

if (( $# > 0 )); then
  roots=("$@")
else
  roots=("${HOME:?HOME precisa estar definido}")
fi

global_path="$(git config --global --get core.hooksPath 2>/dev/null || printf '(não configurado)')"
printf 'core.hooksPath global: %s\n' "$global_path"
found=0

for root in "${roots[@]}"; do
  if [[ ! -d "$root" ]]; then
    printf 'Ignorando pasta inexistente: %s\n' "$root" >&2
    continue
  fi
  while IFS= read -r -d '' marker; do
    repo="${marker%/.git}"
    [[ "$repo" != "$marker" ]] || repo="$root"
    local_path="$(git -C "$repo" config --local --show-origin --get core.hooksPath 2>/dev/null || true)"
    if [[ -n "$local_path" ]]; then
      found=1
      printf '\nAVISO: configuração local substitui os hooks globais\n  Projeto: %s\n  Origem/valor: %s\n' "$repo" "$local_path"
    fi
  done < <(find "$root" -name .git -prune -print0 2>/dev/null)
done

if (( found == 0 )); then printf 'Nenhum core.hooksPath local encontrado nas pastas verificadas.\n'; fi