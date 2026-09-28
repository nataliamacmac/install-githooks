#!/usr/bin/env bash
# install-githooks managed
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
USER_HOME="${GITHOOKS_HOME:-${HOME:?HOME precisa estar definido}}"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$USER_HOME/.config}/git"
HOOK_DIR="$CONFIG_ROOT/hooks"
SCAN_TARGET="$CONFIG_ROOT/scan-custom-hooks.sh"
SCAN_NOW=false

case "${1:-}" in
  "") ;;
  --scan) SCAN_NOW=true ;;
  --help|-h) printf 'Uso: bash install.sh [--scan]\n'; exit 0 ;;
  *) printf 'Argumento desconhecido: %s\n' "$1" >&2; exit 2 ;;
esac

command -v git >/dev/null 2>&1 || { printf 'Git não encontrado.\n' >&2; exit 1; }
command -v bash >/dev/null 2>&1 || { printf 'Bash não encontrado.\n' >&2; exit 1; }
if ! git --version | awk '{ split($3, v, "."); if (v[1] < 2 || (v[1] == 2 && v[2] < 29)) exit 1 }'; then
  printf 'Git 2.29 ou superior é necessário para bloquear commits com --no-verify.\n' >&2
  exit 1
fi

backup_existing() {
  local target="$1" stamp backup suffix
  stamp="$(date +%Y%m%d%H%M%S)"
  backup="$target.backup.$stamp"
  suffix=1
  while [[ -e "$backup" ]]; do
    backup="$target.backup.$stamp.$suffix"
    suffix=$((suffix + 1))
  done
  cp -p "$target" "$backup"
  printf 'Backup: %s\n' "$backup"
}

install_file() {
  local source="$1" target="$2"
  if [[ -e "$target" ]] && cmp -s "$source" "$target"; then return; fi
  if [[ -e "$target" ]] && ! grep -q 'install-githooks managed' "$target" 2>/dev/null; then backup_existing "$target"; fi
  cp "$source" "$target.tmp.$$"
  chmod 755 "$target.tmp.$$"
  mv -f "$target.tmp.$$" "$target"
}

mkdir -p "$HOOK_DIR"
for source in "$PROJECT_DIR"/hooks/*; do
  [[ -f "$source" ]] || continue
  install_file "$source" "$HOOK_DIR/$(basename "$source")"
done
install_file "$PROJECT_DIR/scripts/scan-custom-hooks.sh" "$SCAN_TARGET"

delegated_hooks="applypatch-msg pre-applypatch post-applypatch pre-commit post-commit pre-rebase post-checkout post-merge post-rewrite pre-auto-gc post-index-change fsmonitor-watchman update post-update pre-receive post-receive proc-receive push-to-checkout sendemail-validate"
for hook in $delegated_hooks; do
  target="$HOOK_DIR/$hook"
  if [[ -e "$target" ]] && grep -q 'install-githooks managed' "$target" 2>/dev/null; then continue; fi
  if [[ -e "$target" ]]; then backup_existing "$target"; fi
  printf '#!/bin/sh\n# install-githooks managed\nexec "$(dirname "$0")/_delegar" "%s" "$@"\n' "$hook" > "$target.tmp.$$"
  chmod 755 "$target.tmp.$$"
  mv -f "$target.tmp.$$" "$target"
done

previous_path="$(git config --global --get core.hooksPath 2>/dev/null || true)"
if [[ "$previous_path" != "$HOOK_DIR" ]]; then
  if [[ -n "$previous_path" ]]; then printf 'Substituindo core.hooksPath global anterior: %s\n' "$previous_path"; fi
  git config --global core.hooksPath "$HOOK_DIR"
fi

printf 'Hooks instalados em: %s\n' "$HOOK_DIR"
printf 'Configuração global: %s\n' "$(git config --global --get core.hooksPath)"
if [[ "$SCAN_NOW" == true ]]; then
  bash "$SCAN_TARGET" "$USER_HOME"
else
  printf 'Para procurar core.hooksPath locais: bash "%s" "%s"\n' "$SCAN_TARGET" "$USER_HOME"
fi