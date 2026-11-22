#!/usr/bin/env python3
"""Trava git: hook PreToolUse (Bash) que pede permissão para todo git ou gh que envia algo para o remoto.

Leitura e escrita local passam direto, inclusive fetch, pull, commit e reset.
git push, git send-email e todo gh que altera o remoto devolvem permissionDecision "ask".
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

# Subcomandos git que enviam algo para fora da máquina: mudam o remoto ou mandam e-mail.
GIT_ENVIO = {"push", "send-email"}

# Ações gh que só leem o remoto ou só gravam nesta máquina (clone, checkout, download, config local).
GH_LEITURA = {
    "pr": {"view", "list", "status", "diff", "checks", "checkout"},
    "issue": {"view", "list", "status"},
    "repo": {"view", "list", "clone"},
    "run": {"view", "list", "watch", "download"},
    "workflow": {"view", "list"},
    "release": {"view", "list", "download"},
    "auth": {"status", "token"},
    "config": {"get", "list"},
    "label": {"list"},
    "gist": {"view", "list"},
    "secret": {"list"},
    "variable": {"list", "get"},
    "cache": {"list"},
    "extension": {"list"},
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
    """args começa depois de 'git'. Devolve o motivo, ou None se o comando não envia nada para o remoto."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in GIT_OPCAO_COM_VALOR:
            i += 2
        else:
            i += 1
    if i >= len(args):
        return None  # "git" sozinho ou só com opções globais: só mostra ajuda
    sub = args[i]
    if sub in GIT_ENVIO:
        return "git " + sub + " envia para o remoto"
    return None


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
