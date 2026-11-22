#!/usr/bin/env python3
"""Trava git: hook PreToolUse (Bash) que pede permissão para todo git ou gh que envia algo para o remoto.

Leitura e escrita local passam direto, inclusive fetch, pull, commit e reset.
git push, git send-email, git clean e todo gh que altera o remoto devolvem permissionDecision "ask".
Sessão sem ninguém para responder (claude -p, tarefa agendada, dontAsk) nega a chamada.

Nos projetos da lista de autonomia (${XDG_CONFIG_HOME:-$HOME/.config}/git/autonomia.lista), o mesmo comando recebe
"allow", desde que não mude de repositório (cd, git -C, gh -R, git push para destino que não é remoto nomeado,
owner/repo ou URL de outro repo, $ ou crase no segmento que pede aprovação) e, no gh, aja só no repo da pasta (pr,
issue, release, run, workflow, label, cache, variable, secret, repo sem create e fork, api em repos/<owner>/<repo>).
O casamento com a lista é o de hooks/_somente_leitura, com uma regra a mais: url: vale só se toda URL efetiva de
busca e de envio (com insteadOf) começa com o prefixo. Todo comando que cita autonomia.lista ou roda "autonomia
adicionar" pede aprovação, dentro e fora da lista: só quem usa o terminal põe um projeto na lista.
A lista só leitura vence: repo que casa com ela nunca recebe "allow".
O "allow" vale só para comando em que todo segmento roda git, gh ou um comando de INOFENSIVOS. Com outro comando junto
(git push && rm -rf x), o hook não decide nada e o fluxo normal de permissão do agente decide o comando inteiro.

Fontes na documentação oficial:
- code.claude.com/docs/en/permissions, "Precedence among rules, hooks, and mods": um hook "allow" não passa por cima
  de uma regra "ask" ou "deny" das settings. Por isso o git_guard é a trava única de git e gh.
- code.claude.com/docs/en/hooks, entrada comum dos hooks: o campo "cwd" é a pasta atual da sessão. O repositório
  alvo vem dele.
"""
import json
import os
import re
import subprocess
import sys
import time

# Pontuação que separa comandos em qualquer posição, quando não vem entre aspas.
PONTUACAO = "();<>|&"
PONTUACAO_QUE_SEPARA = set(";&|()")
# Início de subscrito (nome[...]): dentro dos colchetes, o "<<" não abre here-doc.
SUBSCRITO = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\[")
# Palavras do shell: só separam em posição de comando (início do segmento) e quando não vêm entre aspas.
# Em "git push do main", "do" é o destino do push, não palavra do shell.
PALAVRAS_DO_SHELL = {"then", "do", "else", "elif", "if", "while", "until"}
# {, } e ! sem aspas separam em qualquer posição: time { git push; }, function f { git push; }, time ! git push.
SEPARA_EM_QUALQUER_POSICAO = {"{", "}", "!"}
PREFIXOS = {"sudo", "command", "exec", "nohup", "time", "nice", "builtin"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# Na lista de autonomia, o allow vale só para comando em que todo segmento é git (sem git config), gh, um
# destes comandos ou uma palavra de estrutura do shell. Com qualquer outro comando, o hook não decide e o fluxo normal de
# permissão vale. O sed fica de fora: o "w arquivo" do script grava arquivo, também no sed do macOS.
INOFENSIVOS = {"echo", "printf", "cat", "true", "test", "[", "head", "tail", "wc", "grep", "ls", "pwd"}
ESTRUTURA_DO_SHELL = {"for", "case", "fi", "done", "esac"}
# Redirecionamento para estes destinos não grava arquivo: 2>&1, >&2, > /dev/null.
DESTINO_SEM_GRAVACAO = re.compile(r"^(/dev/null|/dev/stdout|/dev/stderr)$")

GIT_OPCAO_COM_VALOR = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path"}

# Subcomandos git que enviam algo para fora da máquina: mudam o remoto ou mandam e-mail.
GIT_ENVIO = {"push", "send-email"}

# Subcomandos git que apagam trabalho local sem volta.
GIT_DESTRUTIVO = {"clean"}

# Opções que levam o comando para outro repositório ou trocam o remoto.
GIT_OPCAO_DE_OUTRO_REPO = {"-C", "-c", "--git-dir", "--work-tree", "--config-env"}
GH_OPCAO_DE_OUTRO_REPO = {"-R", "--repo"}
MUDA_DE_PASTA = {"cd", "pushd", "popd"}
VARIAVEL_DE_OUTRO_REPO = re.compile(r"^(GIT_[A-Za-z0-9_]*|GH_REPO|GH_HOST)=")
ESQUEMA_DE_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
ARQUIVO_DE_CONFIG_GIT = re.compile(r"gitconfig|\.git/config|/git/config")

# O hook tem 10 s. Cada git tem até 2 s, e todos juntos até 6 s.
GIT_TIMEOUT = 2
GIT_PRAZO_TOTAL = 6
INICIO = time.monotonic()

# Grupos gh que agem só no repo da pasta. Os outros (auth, extension, gist, ssh-key, config, org...) pedem aprovação.
GH_ESCOPO_DO_REPO = {"pr", "issue", "release", "run", "workflow", "label", "cache", "variable", "secret"}
GH_OPCAO_DE_OUTRO_ESCOPO = {"--org", "--env", "--user"}
GH_OPCAO_CURTA_DE_OUTRO_ESCOPO = set("oeu")  # -o org, -e env, -u user, também grudadas: -oevil
# Opções gh cujo valor é texto, ramo ou arquivo, nunca repo: o valor não é conferido como alvo.
GH_OPCAO_DE_TEXTO = {"-t", "--title", "-b", "--body", "-F", "--body-file", "-B", "--base", "-H", "--head", "-l",
                     "--label", "-m", "--milestone", "-a", "--assignee", "-r", "--reviewer", "-p", "--project", "-T",
                     "--template", "-n", "--notes", "--notes-file", "--target", "--ref", "-f", "--field",
                     "--raw-field", "--description"}
# owner/repo ou host/owner/repo solto, conferido só onde o gh espera um repo: gh repo <ação> e gh issue transfer.
REPO_SOLTO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+/?$|^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/")
# URL de repo em qualquer posição: https://host/o/r... ou git@host:o/r...
URL_DE_REPO = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+:")
GIT_PUSH_OPCAO_COM_VALOR = {"-o", "--push-option", "--receive-pack", "--exec"}
GH_REPO_FORA_DO_ESCOPO = {"create", "fork"}
GH_API_OPCAO_COM_VALOR = {"-X", "--method", "-H", "--header", "-f", "-F", "--field", "--raw-field", "--input",
                          "-q", "--jq", "-t", "--template", "--cache", "-p", "--preview"}
SUBSTITUICAO = re.compile(r"[$`]")
# No segmento que pede aprovação, $ ou crase no valor citado destas opções não tira a autonomia: o valor é texto, e o
# comando de dentro da substituição é conferido à parte (ler_shell).
OPCAO_DE_TEXTO_LIVRE = {"--title", "-t", "--body", "-b", "--notes", "-n", "-m", "--message", "--subject"}

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


def tokens_do_shell(texto):
    """Divide o texto em [(token, citado, livre)], como o shell: aspas simples, aspas duplas, barra invertida, # de
    comentário e pontuação (PONTUACAO) em token próprio. citado é True se alguma parte do token veio entre aspas ou com
    barra. livre é True se o token tem um $ fora de aspas, que o shell pode dividir em vários argumentos.

    ValueError se uma aspa fica aberta ou o texto termina em barra invertida. Feito à mão porque o shlex em modo
    posix tira as aspas e não diz quais tokens eram citados.
    """
    tokens, atual, citado, livre, comecou = [], [], False, False, False
    i = 0
    while i < len(texto):
        c = texto[i]
        if c in PONTUACAO or c.isspace() or (c == "#" and not comecou):
            if comecou:
                tokens.append(("".join(atual), citado, livre))
            atual, citado, livre, comecou = [], False, False, False
            if c == "#":
                fim = texto.find("\n", i)
                i = len(texto) if fim < 0 else fim
            elif c in PONTUACAO:
                j = i
                while j < len(texto) and texto[j] in PONTUACAO:
                    j += 1
                tokens.append((texto[i:j], False, False))
                i = j
            else:
                i += 1
            continue
        comecou = True
        if c == "\\":
            if i + 1 >= len(texto):
                raise ValueError("barra invertida no fim")
            atual.append(texto[i + 1])
            citado = True
            i += 2
        elif c == "'":
            fim = texto.find("'", i + 1)
            if fim < 0:
                raise ValueError("aspa simples aberta")
            atual.append(texto[i + 1:fim])
            citado = True
            i = fim + 1
        elif c == '"':
            i += 1
            while i < len(texto) and texto[i] != '"':
                if texto[i] == "\\" and i + 1 < len(texto) and texto[i + 1] in '"\\':
                    i += 1
                atual.append(texto[i])
                i += 1
            if i >= len(texto):
                raise ValueError("aspa dupla aberta")
            citado = True
            i += 1
        else:
            atual.append(c)
            livre = livre or c == "$"
            i += 1
    if comecou:
        tokens.append(("".join(atual), citado, livre))
    return tokens


def segmentar(texto):
    """Devolve [(segmento, cortado_por_dolar, livres)]. Separa na pontuação de comando e no $ de "$(" fora de aspas, e
    nas PALAVRAS_DO_SHELL só em posição de comando. cortado_por_dolar diz que o segmento termina num "$(" ou numa crase.
    livres são os índices dos tokens com $ fora de aspas.
    """
    segmentos, atual, livres = [], [], set()
    for tok, citado, livre in tokens_do_shell(texto) + [(";", False, False)]:
        separa = not citado and (set(tok) <= PONTUACAO_QUE_SEPARA or tok == "$" or tok in SEPARA_EM_QUALQUER_POSICAO
                                 or (tok in PALAVRAS_DO_SHELL and not atual))
        if separa:
            if atual:
                segmentos.append((atual, tok == "$", livres))
            atual, livres = [], set()
        else:
            if livre:
                livres.add(len(atual))
            atual.append(tok)
    return segmentos


def quebra_separa(texto):
    """Põe um ; depois de cada quebra de linha. O \n fica no texto, porque o comentário termina nele."""
    return texto.replace("\n", "\n ; ")


def dividir(comando):
    texto = quebra_separa(comando.replace("`", " ; "))
    return [segmento for segmento, _, _ in segmentar(texto)]


def ler_shell(comando):
    """Lê o comando como o shell e devolve (texto_simples, internos, corpos). ValueError se algo fica aberto.

    internos: o texto de dentro de cada $(...), crase, <(...) e >(...) que o shell roda, entre aspas duplas ou não (aspas
    simples são literais), também no corpo de here-doc com delimitador sem aspas. Cada um é conferido como comando
    próprio. texto_simples: o comando sem o corpo dos here-docs, e com cada substituição entre aspas duplas trocada por
    "$()". corpos: o corpo de cada here-doc do comando, com aspas ou sem, para o shell que lê o stdin (bash <<'EOF').
    """
    internos, corpos = [], []
    simples, _ = ler_comando(comando, 0, internos, False, False, 0, corpos=corpos)
    return simples, internos, corpos


def ler_comando(texto, i, internos, dentro, aritmetica, fundo, par="()", corpos=None):
    """Lê comandos a partir de i. dentro: para no par[1] que fecha o par[0] e devolve o índice dele. Devolve (texto, fim).

    Em ${...}, $[...], $((...)), ((...)) e no subscrito nome[...], o shell não abre here-doc: o "<<" é texto.
    """
    if fundo > 20:
        raise ValueError("aninhado demais")
    saida, here_docs, nivel = [], [], 0
    while i < len(texto):
        c = texto[i]
        inicio_de_palavra = i == 0 or texto[i - 1].isspace() or texto[i - 1] in PONTUACAO
        if c == "\\":
            if texto[i + 1:i + 2] != "\n":  # barra + quebra de linha é continuação: o shell apaga as duas
                saida.append(texto[i:i + 2])
            i += 2
        elif c == "'":
            fim = texto.find("'", i + 1)
            if fim < 0:
                raise ValueError("aspa simples aberta")
            saida.append(texto[i:fim + 1])
            i = fim + 1
        elif c == '"':
            conteudo, fim = ler_aspas(texto, i + 1, internos, '"', fundo + 1)
            saida.append('"' + conteudo + '"')
            i = fim + 1
        elif c == "`":
            fim = fim_da_crase(texto, i + 1)
            internos.append(texto[i + 1:fim])
            saida.append(texto[i:fim + 1])
            i = fim + 1
        elif texto.startswith(("${", "$["), i):
            fecha = "{}" if texto[i + 1] == "{" else "[]"
            interno, fim = ler_comando(texto, i + 2, internos, True, True, fundo + 1, fecha, corpos)
            saida.append(texto[i:i + 2] + interno + fecha[1])
            i = fim + 1
        elif inicio_de_palavra and SUBSCRITO.match(texto, i):
            j = SUBSCRITO.match(texto, i).end()
            interno, fim = ler_comando(texto, j, internos, True, True, fundo + 1, "[]", corpos)
            saida.append(texto[i:j] + interno + "]")
            i = fim + 1
        elif texto.startswith(("$(", "<(", ">("), i):
            interno, fim = ler_comando(texto, i + 2, [], True, texto.startswith("$((", i), fundo + 1)
            internos.append(texto[i + 2:fim])
            saida.append(texto[i:i + 2] + interno + ")")
            i = fim + 1
        elif inicio_de_palavra and texto.startswith("((", i):
            interno, fim = ler_comando(texto, i + 1, internos, True, True, fundo + 1, "()", corpos)
            saida.append("(" + interno + ")")
            i = fim + 1
        elif texto.startswith("<<", i) and not texto.startswith("<<<", i) and not aritmetica:
            i = ler_here_doc(texto, i, here_docs, saida)
        elif c == "\n" and here_docs:
            saida.append(c)
            i = ler_corpos(texto, i + 1, here_docs, internos, fundo + 1, corpos)
            here_docs = []
        elif c == "#" and inicio_de_palavra:
            fim = texto.find("\n", i)
            fim = len(texto) if fim < 0 else fim
            saida.append(texto[i:fim])
            i = fim
        elif dentro and inicio_de_palavra and re.match(r"case([\s;&|()]|$)", texto[i:]):
            raise ValueError("case dentro de $(...): o ) do padrão não fecha a substituição")
        elif dentro and c in par:
            if c == par[1] and nivel == 0:
                return "".join(saida), i
            nivel += 1 if c == par[0] else -1
            saida.append(c)
            i += 1
        else:
            saida.append(c)
            i += 1
    if dentro:
        raise ValueError("$( aberto")
    return "".join(saida), i


def ler_aspas(texto, i, internos, fecha, fundo):
    """Lê de i até a aspa dupla que fecha (fecha='"') ou até o fim (fecha=None: corpo de here-doc sem aspas).

    Devolve (conteúdo com cada substituição trocada por "$()", índice da aspa que fecha).
    """
    partes = []
    while i < len(texto) and texto[i] != fecha:
        if texto[i] == "\\":
            if texto[i + 1:i + 2] != "\n":  # continuação de linha, também entre aspas duplas
                partes.append(texto[i:i + 2])
            i += 2
        elif texto[i] == "`":
            fim = fim_da_crase(texto, i + 1)
            internos.append(texto[i + 1:fim])
            partes.append("$()")
            i = fim + 1
        elif texto.startswith("$(", i):
            _, fim = ler_comando(texto, i + 2, [], True, texto.startswith("$((", i), fundo + 1)
            internos.append(texto[i + 2:fim])
            partes.append("$()")
            i = fim + 1
        else:
            partes.append(texto[i])
            i += 1
    if fecha and i >= len(texto):
        raise ValueError("aspa dupla aberta")
    return "".join(partes), i


def fim_da_crase(texto, i):
    while i < len(texto) and texto[i] != "`":
        i += 2 if texto[i] == "\\" else 1
    if i >= len(texto):
        raise ValueError("crase aberta")
    return i


def ler_here_doc(texto, i, here_docs, saida):
    """Lê "<<[-]palavra" a partir de i e guarda (delimitador, tira_tabs, citado) em here_docs. Devolve o fim."""
    j = i + 2
    tira_tabs = texto.startswith("-", j)
    j += tira_tabs
    while j < len(texto) and texto[j] in " \t":
        j += 1
    k = j
    while k < len(texto) and not texto[k].isspace() and texto[k] not in PONTUACAO:
        if texto[k] in "'\"":
            fim = texto.find(texto[k], k + 1)
            if fim < 0:
                raise ValueError("aspa aberta no delimitador do here-doc")
            k = fim + 1
        else:
            k += 2 if texto[k] == "\\" else 1
    palavra = texto[j:k]
    delimitador = re.sub(r"['\"\\]", "", palavra)
    if not delimitador:
        raise ValueError("here-doc sem delimitador")
    here_docs.append((delimitador, tira_tabs, delimitador != palavra))
    saida.append(texto[i:k])
    return k


def ler_corpos(texto, i, here_docs, internos, fundo, corpos=None):
    """Pula o corpo de cada here-doc a partir de i, que é o início da linha seguinte. Devolve o índice depois do último.

    Com delimitador sem aspas, o shell expande o corpo: as substituições dele vão para internos. Todo corpo vai para
    corpos, quando a lista existe.
    """
    for delimitador, tira_tabs, citado in here_docs:
        inicio, fim_do_corpo = i, None
        while i < len(texto) and fim_do_corpo is None:
            fim = texto.find("\n", i)
            fim = len(texto) if fim < 0 else fim
            linha = texto[i:fim]
            if (linha.lstrip("\t") if tira_tabs else linha) == delimitador:
                fim_do_corpo = i
            i = fim + 1
        if fim_do_corpo is None:
            raise ValueError("here-doc sem a linha do delimitador")
        if corpos is not None:
            corpos.append(texto[inicio:fim_do_corpo])
        if not citado:
            ler_aspas(texto[inicio:fim_do_corpo], 0, internos, None, fundo)
    return min(i, len(texto))


def tirar_prefixos(args):
    i = 0
    while i < len(args):
        a = args[i]
        nome = os.path.basename(a)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", a):  # x=1, a[1]=3, x+=1
            i += 1
        elif re.match(r"^[^=]*\]\+?=", a):  # resto de a[$(x)]=3, que o $( corta em dois segmentos
            i += 1
        elif SUBSCRITO.match(a):  # a[1 + 1]=3: o subscrito com espaço vira vários tokens, até o ]=
            fim = next((j for j in range(i, len(args)) if re.search(r"\]\+?=", args[j])), None)
            if fim is None:
                break
            i = fim + 1
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
    if sub in GIT_DESTRUTIVO:
        return "git " + sub + " apaga arquivos que o git não guarda"
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
        texto, internos, corpos = ler_shell(comando)
        segmentos = dividir(texto)
    except ValueError:
        if re.search(r"\b(git|gh)\b", comando):
            return "não consegui interpretar o comando, que cita git ou gh"
        return None
    if any(shell_le_o_stdin(seg) for seg in segmentos):
        internos = internos + corpos  # bash <<'EOF': o corpo do here-doc roda como comando
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
    for interno in internos:
        motivo = analisar(interno, profundidade + 1)
        if motivo:
            return motivo
    return None


def shell_le_o_stdin(segmento):
    """True se o segmento roda um shell de SHELLS sem -c: o shell lê os comandos do stdin (bash <<'EOF', | bash)."""
    args = tirar_prefixos(segmento)
    return bool(args) and os.path.basename(args[0]) in SHELLS and "-c" not in args[1:]


def sem_aspas(comando):
    """Tira aspas simples, aspas duplas e barras invertidas e junta espaços: autonomia.li'sta' vira autonomia.lista."""
    return re.sub(r"\s+", " ", re.sub(r"['\"\\]", "", comando))


def mexe_na_autonomia(comando):
    """Devolve o motivo quando o comando cita a lista de autonomia ou roda "autonomia adicionar".

    Antes de procurar, tira aspas e barras invertidas e junta espaços: autonomia.li'sta' também casa. Nome montado em
    variável ainda escapa; é limite aceito.
    """
    comando = sem_aspas(comando)
    if re.search(r"autonomia(\.l|[?*\[])", comando):
        return "o comando cita a lista de autonomia"
    if re.search(r"autonomia\b[\s\S]*\badicionar\b", comando):
        return "o comando roda autonomia adicionar"
    return None


def git(cwd, *args):
    """Roda git na pasta e devolve a saída, ou None em qualquer falha."""
    restante = GIT_PRAZO_TOTAL - (time.monotonic() - INICIO)
    if restante <= 0:
        return None
    try:
        processo = subprocess.run(["git", "-C", cwd, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=min(GIT_TIMEOUT, restante))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if processo.returncode != 0:
        return None
    return processo.stdout.decode("utf-8", "replace")


def sem_credencial(url):
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@", r"\1", url)


def pasta_do_repo(cwd):
    """O repo principal, também para worktrees, como em hooks/_somente_leitura. None fora de repo."""
    comum = git(cwd, "rev-parse", "--git-common-dir")
    if not comum or not comum.strip():
        return None
    comum = os.path.realpath(os.path.join(cwd, comum.strip()))
    return comum[:-len("/.git")] if comum.endswith("/.git") else comum


def remotos_do_repo(cwd):
    """Devolve (nomes dos remotos, URLs efetivas de busca e de envio com insteadOf e pushInsteadOf, sem credencial).

    None se algum git falha: sem a lista inteira, nenhuma entrada url: pode valer.
    """
    saida = git(cwd, "remote")
    if saida is None:
        return None
    remotos, urls = saida.split(), []
    for remoto in remotos:
        for modo in ((), ("--push",)):
            saida = git(cwd, "remote", "get-url", *modo, "--all", remoto)
            if saida is None:
                return None
            urls += [sem_credencial(linha.strip()) for linha in saida.splitlines() if linha.strip()]
    return remotos, urls


def entradas_da_lista(nome="autonomia.lista"):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.environ.get("HOME") or os.path.expanduser("~"),
                                                             ".config")
    try:
        with open(os.path.join(base, "git", nome), encoding="utf-8", errors="replace") as arquivo:
            return [linha.strip() for linha in arquivo]
    except OSError:
        return []


def so_leitura(cwd, repo, urls):
    """True se o repo casa com a lista só leitura, como em hooks/_somente_leitura: url: casa com QUALQUER URL.

    Confere as URLs do config e as efetivas. Se o git falha e a lista tem entrada url:, conta como só leitura.
    """
    entradas = [e for e in entradas_da_lista("somente-leitura.lista")
                if (e.startswith("url:") and len(e) > 4) or (e.startswith("pasta:") and len(e) > 6)]
    if not entradas:
        return False
    config = git(cwd, "config", "--get-regexp", r"^remote\..*\.(url|pushurl)$")
    if urls is None or (config is None and any(e.startswith("url:") for e in entradas)):
        return True
    todas = list(urls) + [sem_credencial(linha.split(" ", 1)[1].strip())
                          for linha in (config or "").splitlines() if " " in linha]
    for entrada in entradas:
        if entrada.startswith("url:") and any(url.startswith(entrada[4:]) for url in todas):
            return True
        if entrada.startswith("pasta:"):
            pasta = os.path.realpath(entrada[6:])
            if os.path.isdir(pasta) and (repo + "/").startswith(pasta + "/"):
                return True
    return False


def entrada_de_autonomia(cwd):
    """Devolve a entrada da lista de autonomia que casa com o repo da pasta cwd, ou None. Nunca lança exceção."""
    return autonomia_do_repo(cwd)[0]


def autonomia_do_repo(cwd):
    """Devolve (entrada, remotos, urls efetivas) do repo da pasta cwd, ou (None, [], []). Nunca lança exceção.

    url: vale só se TODA URL efetiva começa com o prefixo. pasta: vale para todo remoto do repo dentro da pasta.
    Repo da lista só leitura nunca tem autonomia.
    """
    try:
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.isdir(cwd):
            return None, [], []
        repo = pasta_do_repo(cwd)
        if not repo:
            return None, [], []
        remotos, urls = remotos_do_repo(cwd) or ([], None)
        if so_leitura(cwd, repo, urls):
            return None, [], []  # a lista só leitura vence: o comando pede aprovação
        for entrada in entradas_da_lista():
            if entrada.startswith("url:") and len(entrada) > 4:
                if urls and all(url.startswith(entrada[4:]) for url in urls):
                    return entrada, remotos, urls
            elif entrada.startswith("pasta:") and len(entrada) > 6:
                pasta = os.path.realpath(entrada[6:])
                if os.path.isdir(pasta) and (repo + "/").startswith(pasta + "/"):
                    return entrada, remotos, urls or []
    except Exception:
        return None, [], []
    return None, [], []


def caminho_de_repo(endereco):
    """'https://u@host/o/r.git', 'git@host:o/r.git' e 'host/o/r' viram 'host/o/r', sem barra no fim."""
    texto = endereco.strip()
    com_esquema = ESQUEMA_DE_URL.match(texto)
    texto = ESQUEMA_DE_URL.sub("", texto)
    texto = re.sub(r"^[^/@]*@", "", texto)
    if not com_esquema:
        texto = re.sub(r"^([^/:]+):", r"\1/", texto)  # forma scp: git@host:o/r
    texto = texto.rstrip("/")
    if texto.endswith(".git"):
        texto = texto[:-4]
    return texto.lower()


def e_deste_repo(endereco, urls):
    """True se o endereço (o/r, host/o/r ou URL) aponta para um dos remotos do repo."""
    alvo = caminho_de_repo(endereco)
    if not alvo or ".." in alvo.split("/"):
        return False
    if alvo.count("/") == 1:
        alvo = "github.com/" + alvo  # o/r sem host: o gh usa o github.com
    for url in urls:
        remoto = caminho_de_repo(url)
        if not remoto:
            continue
        if alvo == remoto or remoto.endswith("/" + alvo) or alvo.startswith(remoto + "/"):
            return True
    return False


def git_muda_de_repo(args, remotos):
    """args começa depois de 'git'."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i].split("=", 1)[0] in GIT_OPCAO_DE_OUTRO_REPO:
            return True
        i += 2 if args[i] in GIT_OPCAO_COM_VALOR else 1
    if i >= len(args):
        return False
    if args[i] == "remote":
        return any(a in {"add", "set-url", "rename"} for a in args[i + 1:])
    if args[i] == "config":
        return any(re.search(r"url|remote\.|insteadof", a, re.IGNORECASE) for a in args[i + 1:])
    if args[i] != "push":
        return False
    resto = args[i + 1:]
    j = 0
    while j < len(resto):
        arg = resto[j]
        if arg == "--repo" or arg.startswith("--repo="):
            return True
        if arg == "--":
            return j + 1 < len(resto) and resto[j + 1] not in remotos
        if arg in GIT_PUSH_OPCAO_COM_VALOR:
            j += 2
            continue
        if not arg.startswith("-"):
            # O primeiro argumento solto é o destino: só um remoto configurado do repo passa.
            return arg not in remotos
        j += 1
    return False


def gh_muda_de_repo(args, urls):
    """args começa depois de 'gh'."""
    if any(a.split("=", 1)[0] in GH_OPCAO_DE_OUTRO_REPO or (a.startswith("-R") and len(a) > 2) for a in args):
        return True
    soltos = soltos_do_gh(args)
    if not soltos:
        return False
    grupo = soltos[0]
    if grupo == "api":
        return not api_do_repo(args[args.index("api") + 1:], urls)
    if grupo == "repo":
        if len(soltos) < 2 or soltos[1] in GH_REPO_FORA_DO_ESCOPO:
            return True
    elif grupo not in GH_ESCOPO_DO_REPO:
        return True
    elif any(opcao_de_outro_escopo(a) for a in args):
        return True
    # Fora de gh repo e gh issue transfer, posicional com barra é branch, tag ou caminho (gh pr merge feature/x).
    espera_repo = grupo == "repo" or soltos[1:2] == ["transfer"]
    for arg in soltos[1:]:
        if URL_DE_REPO.match(arg) or (espera_repo and REPO_SOLTO.match(arg)):
            if not e_deste_repo(arg, urls):
                return True
    return False


def soltos_do_gh(args):
    """Argumentos soltos do gh, sem o valor das opções de texto (--title, --head...)."""
    soltos, i = [], 0
    while i < len(args):
        if args[i] in GH_OPCAO_DE_TEXTO:
            i += 2
            continue
        if not args[i].startswith("-"):
            soltos.append(args[i])
        i += 1
    return soltos


def opcao_de_outro_escopo(arg):
    """--org, --env, --user (também com =) e -o, -e, -u (também grudadas: -oevil)."""
    if arg.startswith("--"):
        return arg.split("=", 1)[0] in GH_OPCAO_DE_OUTRO_ESCOPO
    return arg.startswith("-") and len(arg) > 1 and arg[1] in GH_OPCAO_CURTA_DE_OUTRO_ESCOPO


def api_do_repo(args, urls):
    """True se o caminho de "gh api" é do próprio repo: repos/<owner>/<repo>[/...] ou repos/{owner}/{repo}[/...]."""
    caminho = None
    i = 0
    while i < len(args):
        if args[i] == "--hostname" or args[i].startswith("--hostname="):
            return False
        if args[i] in GH_API_OPCAO_COM_VALOR:
            i += 2
            continue
        if not args[i].startswith("-") and caminho is None:
            caminho = args[i]
        i += 1
    if not caminho or "%" in caminho:
        return False
    partes = caminho.split("?", 1)[0].lstrip("/").split("/")
    if len(partes) < 3 or partes[0] != "repos" or ".." in partes:
        return False
    if partes[1:3] == ["{owner}", "{repo}"]:
        return True
    return e_deste_repo(partes[1] + "/" + partes[2], urls)


def segmento_pede_aprovacao(segmento):
    """True se o segmento roda git ou gh com motivo para pedir aprovação."""
    args = tirar_prefixos(segmento)
    if not args:
        return False
    nome = os.path.basename(args[0])
    if nome == "git":
        return git_altera(args[1:]) is not None
    if nome == "gh":
        return gh_altera(args[1:]) is not None
    return False


def tem_substituicao_em_git_ou_gh(comando):
    """True se o segmento git ou gh que pede aprovação tem $ ou crase: o valor só aparece quando o shell roda.

    Segmenta como o dividir (segmentar). Um segmento tem expansão se um token dele tem $ ou se ele é cortado por $(
    ou pela crase que abre: git push $(cat f) main.
    Segmento git ou gh sem motivo (git commit -m "$(cat m)") não conta: o comando de dentro é conferido à parte.
    O valor de OPCAO_DE_TEXTO_LIVRE com $ só entre aspas também não conta: gh pr create --body "$(cat f)".
    O comando chega já lido pelo ler_shell: substituição entre aspas é "$()", e crase só sobra fora de aspas.
    """
    partes = comando.split("`")
    texto = partes[0] + "".join((" $ ; " if n % 2 else " ; ") + parte for n, parte in enumerate(partes[1:], 1))
    try:
        segmentos = segmentar(quebra_separa(texto))
    except ValueError:
        return True
    for segmento, cortado_por_dolar, livres in segmentos:
        if not segmento_pede_aprovacao(segmento):
            continue
        if cortado_por_dolar:
            return True
        textos = valores_de_texto(segmento)
        if any(SUBSTITUICAO.search(t) and (k in livres or k not in textos) for k, t in enumerate(segmento)):
            return True
    return False


def valores_de_texto(segmento):
    """Índices dos tokens que são valor de OPCAO_DE_TEXTO_LIVRE: --body x, -m x, --title=x."""
    valores, k = set(), 0
    while k < len(segmento):
        token = segmento[k]
        if token in OPCAO_DE_TEXTO_LIVRE:
            valores.add(k + 1)
            k += 2
        elif token in GH_OPCAO_DE_TEXTO:
            k += 2
        else:
            if token.startswith("--") and token.split("=", 1)[0] in OPCAO_DE_TEXTO_LIVRE and "=" in token:
                valores.add(k)
            k += 1
    return valores


def muda_de_repo(comando, urls=(), remotos=(), profundidade=0):
    """True se o comando pode agir em outro repo além do da pasta atual. Na dúvida, True."""
    if profundidade > 3 or ARQUIVO_DE_CONFIG_GIT.search(sem_aspas(comando)):
        return True
    try:
        texto, internos, corpos = ler_shell(comando)
        segmentos = dividir(texto)
    except ValueError:
        return True
    if tem_substituicao_em_git_ou_gh(texto):
        return True
    if corpos and any(shell_le_o_stdin(seg) for seg in segmentos):
        return True  # o corpo do here-doc roda como comando e não é conferido como alvo
    for seg in segmentos:
        if any(VARIAVEL_DE_OUTRO_REPO.match(a) for a in seg):
            return True
        if any(os.path.basename(a) == "xargs" for a in seg):
            return True  # os argumentos chegam pelo stdin e não dá para conferir
        args = tirar_prefixos(seg)
        if not args:
            continue
        nome = os.path.basename(args[0])
        if nome in MUDA_DE_PASTA:
            return True
        if nome == "git" and git_muda_de_repo(args[1:], remotos):
            return True
        if nome == "gh" and gh_muda_de_repo(args[1:], urls):
            return True
        if nome in SHELLS and "-c" in args[1:]:
            k = args.index("-c", 1)
            if k + 1 < len(args) and muda_de_repo(args[k + 1], urls, remotos, profundidade + 1):
                return True
        if nome == "eval" and muda_de_repo(" ".join(args[1:]), urls, remotos, profundidade + 1):
            return True
    return any(muda_de_repo(interno, urls, remotos, profundidade + 1) for interno in internos)


def so_git_gh_ou_inofensivo(comando, profundidade=0):
    """True se todo segmento do comando, também os de $(...), crase e here-doc, roda git, gh ou INOFENSIVOS.

    O nome do comando vale sem caminho (./echo não conta), sem sudo na frente e sem redirecionamento que grava arquivo.
    Na dúvida, False.
    """
    if profundidade > 3:
        return False
    try:
        texto, internos, _ = ler_shell(comando)
        segmentos = dividir(texto)
    except ValueError:
        return False
    for seg in segmentos:
        args = tirar_prefixos(seg)
        if any(os.path.basename(a) in {"sudo", "doas"} for a in seg[:len(seg) - len(args)]) or grava_arquivo(seg):
            return False
        if not args:
            continue
        if args[0] not in {"git", "gh"} | INOFENSIVOS | ESTRUTURA_DO_SHELL:
            return False
        if args[0] == "git" and e_git_config(args[1:]):
            return False
    return all(so_git_gh_ou_inofensivo(interno, profundidade + 1) for interno in internos)


def e_git_config(args):
    """args começa depois de 'git'. True se o subcomando é config, de leitura ou de gravação.

    Uma chave como credential.helper, core.sshCommand ou core.hooksPath roda comando no git seguinte, e separar leitura
    de gravação pelo texto falha (git config chave valor --get grava).
    """
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_OPCAO_COM_VALOR else 1
    return i < len(args) and args[i] == "config"


def grava_arquivo(segmento):
    """True se o segmento tem redirecionamento de saída para arquivo: > f, >> f, &> f. 2>&1 e > /dev/null não contam."""
    for k, token in enumerate(segmento):
        if ">" in token and set(token) <= set(PONTUACAO):
            destino = segmento[k + 1] if k + 1 < len(segmento) else ""
            if DESTINO_SEM_GRAVACAO.match(destino) or ("&" in token and re.match(r"^(\d+|-)$", destino)):
                continue
            return True
    return False


def responder(decisao, motivo):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decisao,
            "permissionDecisionReason": motivo,
        }
    }, ensure_ascii=False))


def main():
    try:
        dados = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(dados, dict) or dados.get("tool_name") != "Bash":
        return 0
    entrada_da_ferramenta = dados.get("tool_input")
    comando = entrada_da_ferramenta.get("command") if isinstance(entrada_da_ferramenta, dict) else None
    if not isinstance(comando, str):
        return 0
    motivo = mexe_na_autonomia(comando)
    if motivo:
        responder("ask", "Trava git (" + motivo + "). Confira o comando antes de aprovar.")
        return 0
    if not re.search(r"\b(git|gh)\b", comando):
        return 0
    motivo = analisar(comando)
    if not motivo:
        return 0
    cwd = dados.get("cwd")
    entrada, remotos, urls = autonomia_do_repo(cwd)
    if entrada and not muda_de_repo(comando, urls, remotos):
        if so_git_gh_ou_inofensivo(comando):
            responder("allow", "Autonomia (" + entrada + ")")
        return 0  # outro comando junto: o git_guard não libera o resto, e o fluxo normal de permissão decide
    responder("ask", "Trava git (" + motivo + "). Confira o comando antes de aprovar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
