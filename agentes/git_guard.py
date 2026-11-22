#!/usr/bin/env python3
"""Trava git: hook PreToolUse (Bash) que pede permissão para todo git ou gh que altera algo.

Leitura (status, log, diff, show...) passa direto. Escrita, acesso a remoto e
qualquer subcomando desconhecido devolvem permissionDecision "ask".
Sessão sem ninguém para responder (claude -p, tarefa agendada, dontAsk) nega a chamada.
"""
import json
import os
import re
import shlex
import sys

SEPARADORES = {";", "&", "&&", "|", "||", "(", ")", "{", "}", "!", "then", "do", "else", "elif"}
PREFIXOS = {"sudo", "command", "exec", "nohup", "time", "nice", "builtin"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}

GIT_OPCAO_COM_VALOR = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path"}

# Subcomandos que só leem, sem argumento que mude algo.
GIT_LEITURA = {
    "status", "log", "diff", "show", "blame", "annotate", "grep", "ls-files", "ls-tree",
    "rev-parse", "rev-list", "describe", "shortlog", "cat-file", "help", "version",
    "whatchanged", "difftool", "range-diff", "merge-base", "name-rev", "show-ref",
    "for-each-ref", "check-ignore", "check-attr", "count-objects", "var", "cherry",
    "show-branch", "diff-tree", "diff-files", "diff-index", "verify-commit", "verify-tag",
}

BRANCH_ESCRITA = {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "-u", "--delete", "--move", "--copy",
                  "--force", "--set-upstream-to", "--unset-upstream", "--edit-description", "--track",
                  "--no-track"}
BRANCH_LISTA = {"-a", "-r", "-v", "-vv", "-l", "--list", "--all", "--remotes", "--show-current",
                "--verbose", "--merged", "--no-merged", "--contains", "--no-contains", "--sort",
                "--format", "--color", "--no-color", "--column", "--no-column", "--points-at"}

# git tag (git-scm.com/docs/git-tag): sem nome de tag, ou com uma opção que implica --list, o comando só lista.
TAG_ESCRITA = {"-a", "--annotate", "-s", "--sign", "--no-sign", "-u", "--local-user", "-f", "--force", "-d",
               "--delete", "-m", "--message", "-F", "--file", "-e", "--edit", "--cleanup", "--create-reflog",
               "--trailer"}
TAG_LISTA = {"-l", "--list", "-n", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
             "-v", "--verify"}

GH_LEITURA = {
    "pr": {"view", "list", "status", "diff", "checks"},
    "issue": {"view", "list", "status"},
    "repo": {"view", "list"},
    "run": {"view", "list", "watch"},
    "workflow": {"view", "list"},
    "release": {"view", "list"},
    "auth": {"status"},
    "search": None,  # todo "gh search ..." só lê
    "browse": None,
    "status": None,
    "help": None,
    "version": None,
}


def dividir(comando):
    texto = comando.replace("`", " ; ").replace("\n", " ; ")
    lexer = shlex.shlex(texto, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    segmentos, atual = [], []
    for tok in tokens:
        if tok in SEPARADORES or tok == "$":
            if atual:
                segmentos.append(atual)
            atual = []
        else:
            atual.append(tok)
    if atual:
        segmentos.append(atual)
    return segmentos


def tirar_prefixos(args):
    i = 0
    while i < len(args):
        a = args[i]
        nome = os.path.basename(a)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a):
            i += 1
        elif nome == "env":
            i += 1
            while i < len(args) and (args[i].startswith("-") or "=" in args[i]):
                i += 1
        elif nome in PREFIXOS:
            i += 1
            while i < len(args) and args[i].startswith("-"):
                i += 1
        elif nome == "timeout":
            i += 1
            while i < len(args) and args[i].startswith("-"):
                i += 1
            i += 1  # duração
        elif nome == "xargs":
            i += 1
            while i < len(args) and args[i].startswith("-"):
                if args[i] in {"-I", "-n", "-P", "-L", "-d", "-E", "-s"}:
                    i += 1
                i += 1
        else:
            break
    return args[i:]


def git_altera(args):
    """args começa depois de 'git'. Devolve o motivo, ou None se só lê."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in GIT_OPCAO_COM_VALOR:
            i += 2
        else:
            i += 1
    if i >= len(args):
        return None  # "git" sozinho ou só com opções globais: só mostra ajuda
    sub, resto = args[i], args[i + 1:]

    if sub in GIT_LEITURA:
        return None
    if sub == "branch":
        opcoes = {r.split("=")[0] for r in resto if r.startswith("-")}
        posicionais = [r for r in resto if not r.startswith("-")]
        if not (opcoes & BRANCH_ESCRITA) and (not posicionais or opcoes & BRANCH_LISTA):
            return None
        return "git branch que cria, apaga ou renomeia"
    if sub == "tag":
        opcoes = {"-n" if re.match(r"^-n[0-9]+$", r) else r.split("=")[0] for r in resto if r.startswith("-")}
        posicionais = [r for r in resto if not r.startswith("-")]
        if not (opcoes & TAG_ESCRITA) and (not posicionais or opcoes & TAG_LISTA):
            return None
        return "git tag que cria ou apaga"
    if sub == "remote":
        if not resto or resto[0] in {"-v", "--verbose", "show", "get-url"}:
            if resto and resto[0] == "show" and "-n" not in resto:
                return "git remote show acessa o remoto"
            return None
        return "git remote " + resto[0]
    if sub == "stash":
        if resto and resto[0] in {"list", "show"}:
            return None
        return "git stash altera a árvore de trabalho"
    if sub == "worktree":
        if resto and resto[0] == "list":
            return None
        return "git worktree altera worktrees"
    if sub == "config":
        leitura = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch", "--show-origin"}
        if set(resto) & leitura:
            return None
        valores = [r for r in resto if not r.startswith("-")]
        if len(valores) == 1:
            return None  # git config chave (lê o valor)
        return "git config grava configuração"
    if sub == "reflog":
        if not resto or resto[0] in {"show", "exists"}:
            return None
        return "git reflog " + resto[0]
    if sub == "notes":
        if not resto or resto[0] in {"list", "show"}:
            return None
        return "git notes altera notas"
    if sub == "submodule":
        if resto and resto[0] in {"status", "summary"}:
            return None
        return "git submodule altera submódulos"
    if sub in {"fetch", "pull", "push", "ls-remote", "clone", "remote-prune"}:
        return "git " + sub + " acessa o remoto"
    return "git " + sub + " altera o repositório"


def gh_altera(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in {"-R", "--repo", "--hostname"} else 1
    if i >= len(args):
        return None
    grupo, resto = args[i], args[i + 1:]
    if grupo == "api":
        metodo = "GET"
        for j, r in enumerate(resto):
            if r in {"-X", "--method"} and j + 1 < len(resto):
                metodo = resto[j + 1].upper()
            elif r.startswith("--method="):
                metodo = r.split("=", 1)[1].upper()
            elif r.startswith("-X") and len(r) > 2:
                metodo = r[2:].upper()
            elif r in {"-f", "-F", "--field", "--raw-field", "--input"} or r.startswith(("--field=", "--raw-field=", "--input=")):
                if metodo == "GET":
                    metodo = "POST"
        return None if metodo == "GET" else "gh api " + metodo
    if grupo in GH_LEITURA:
        permitidos = GH_LEITURA[grupo]
        if permitidos is None:
            return None
        acao = next((r for r in resto if not r.startswith("-")), None)
        if acao in permitidos:
            return None
        return "gh " + grupo + " " + (acao or "")
    return "gh " + grupo + " altera o remoto"


def analisar(comando, profundidade=0):
    if profundidade > 3:
        return "comando aninhado demais para conferir"
    try:
        segmentos = dividir(comando)
    except ValueError:
        if re.search(r"\b(git|gh)\b", comando):
            return "não consegui interpretar o comando, que cita git ou gh"
        return None
    for seg in segmentos:
        args = tirar_prefixos(seg)
        if not args:
            continue
        nome = os.path.basename(args[0])
        if nome == "git":
            motivo = git_altera(args[1:])
        elif nome == "gh":
            motivo = gh_altera(args[1:])
        elif nome in SHELLS and "-c" in args[1:]:
            k = args.index("-c", 1)
            motivo = analisar(args[k + 1], profundidade + 1) if k + 1 < len(args) else None
        elif nome == "eval":
            motivo = analisar(" ".join(args[1:]), profundidade + 1)
        else:
            motivo = None
        if motivo:
            return motivo
    return None


def main():
    try:
        dados = json.load(sys.stdin)
    except Exception:
        return 0
    if dados.get("tool_name") != "Bash":
        return 0
    comando = (dados.get("tool_input") or {}).get("command") or ""
    if not re.search(r"\b(git|gh)\b", comando):
        return 0
    motivo = analisar(comando)
    if not motivo:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "Trava git (" + motivo + "). Confira o comando antes de aprovar.",
        }
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
