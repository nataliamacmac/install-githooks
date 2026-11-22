"""Testes do git_guard (hook PreToolUse do Claude Code) e do instalador em agentes/instalar.

O instalador roda pelo install.sh com GITHOOKS_HOME e CLAUDE_CONFIG_DIR temporários. A configuração real da máquina
não muda.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
GUARDA = PROJECT / "agentes" / "git_guard.py"


def carregar_guarda():
    spec = importlib.util.spec_from_file_location("git_guard", GUARDA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class GitGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guarda = carregar_guarda()

    def test_comandos_que_nao_enviam_nada_ao_remoto_passam(self):
        for comando in ("git status", "git log --oneline -5", "git diff HEAD~1", "git worktree list",
                        "git branch", "git tag -l 'v1*'", "git config --get user.email", "git stash list",
                        "git -C /tmp/x status --short", "gh pr view 12", "gh api repos/o/r/pulls",
                        "cd x && git log | head -3", "ls -la", "git fetch", "git fetch --prune", "git pull",
                        "git ls-remote origin", "git commit -m x", "git add .", "git branch -D a", "git tag v1.0",
                        "git config --local x.y z", "git stash", "git reset --hard", "git remote prune origin"):
            self.assertIsNone(self.guarda.analisar(comando), comando)

    def test_gh_que_so_le_ou_grava_na_maquina_passa(self):
        for comando in ("gh auth token", "GH_TOKEN=$(gh auth token) python3 x.py", "gh auth status",
                        "gh config get git_protocol", "gh config list", "gh repo clone o/r", "gh pr checkout 12",
                        "gh run download 5", "gh release download v1", "gh label list", "gh gist list",
                        "gh secret list", "gh variable list", "gh cache list", "gh extension list"):
            self.assertIsNone(self.guarda.analisar(comando), comando)

    def test_gh_auth_que_muda_a_credencial_pede_aprovacao(self):
        for comando in ("gh auth login", "gh auth refresh -s repo", "gh auth logout"):
            self.assertIsNotNone(self.guarda.analisar(comando), comando)

    def test_comandos_que_enviam_ao_remoto_pedem_aprovacao(self):
        for comando in ("git push origin main", "git push origin --delete velha", "git -C /tmp/x push",
                        "git send-email x.patch", "gh pr create", "gh pr merge 3", "gh repo delete o/r",
                        "gh api -X POST repos/o/r/issues", "bash -c 'git push'", "git log && git push"):
            self.assertIsNotNone(self.guarda.analisar(comando), comando)

    def test_saida_do_hook_pede_aprovacao_em_json(self):
        saida = self.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})

        dados = json.loads(saida)["hookSpecificOutput"]
        self.assertEqual("PreToolUse", dados["hookEventName"])
        self.assertEqual("ask", dados["permissionDecision"])
        self.assertIn("envia para o remoto", dados["permissionDecisionReason"])

    def test_hook_fica_em_silencio_para_leitura_e_outras_ferramentas(self):
        self.assertEqual("", self.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
        self.assertEqual("", self.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": "git fetch"}}))
        self.assertEqual("", self.rodar_guarda({"tool_name": "Read", "tool_input": {"file_path": "/x"}}))

    def test_git_dentro_de_if_while_until_pede_aprovacao(self):
        for comando in ("if git push; then :; fi", "while git push; do :; done", "until gh pr merge 3; do :; done",
                        "if ! git push; then :; fi", "true; { git push; }", "x || ( git push )"):
            self.assertIsNotNone(self.guarda.analisar(comando), comando)

    def test_palavra_do_shell_fora_da_posicao_de_comando_nao_separa(self):
        self.assertEqual([["git", "push", "do", "main"]], self.guarda.dividir("git push do main"))
        self.assertEqual([["git", "push", "do", "main"]], self.guarda.dividir("git push 'do' main"))
        self.assertEqual([["git", "push", ";", "main"]], self.guarda.dividir("git push ';' main"))
        self.assertEqual([["true"], ["git", "push"], ["fi"]], self.guarda.dividir("if true; then git push; fi"))
        self.assertEqual([["echo", "then"]], self.guarda.dividir("echo then"))
        self.assertEqual([["echo", "if"]], self.guarda.dividir("echo \"if\""))

    def test_git_clean_pede_aprovacao_fora_da_lista(self):
        self.assertIsNotNone(self.guarda.analisar("git clean -fd"))

    @staticmethod
    def rodar_guarda(entrada, cwd=None, env=None):
        processo = subprocess.run([sys.executable, "-I", str(GUARDA)], input=json.dumps(entrada).encode(),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, env=env)
        return processo.stdout.decode().strip()


class AutonomiaDoGitGuardTest(unittest.TestCase):
    """O hook roda com HOME, XDG_CONFIG_HOME e repos temporários. A lista real da máquina não é lida."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="githooks-autonomia-")
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name).resolve()
        self.home = self.temp / "home"
        self.home.mkdir()
        global_ = self.temp / "gitconfig"
        global_.touch()
        self.env = {chave: valor for chave, valor in os.environ.items() if not chave.startswith(("GIT_", "XDG_"))}
        self.env.update({"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                         "GIT_CONFIG_GLOBAL": str(global_), "GIT_CONFIG_NOSYSTEM": "1"})
        self.projeto = self.criar_repo("projeto", "https://github.com/o/r.git")
        self.outro = self.criar_repo("outro", "https://github.com/x/y.git")
        self.escrever_lista("pasta:" + str(self.projeto))

    def test_projeto_da_lista_recebe_allow(self):
        for comando in ("git push --force origin main", "gh repo delete o/r", "git clean -fdx"):
            decisao, motivo = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)
            self.assertTrue(motivo.startswith("Autonomia (pasta:"), motivo)

    def test_url_da_lista_casa_sem_credencial(self):
        self.git(self.projeto, "remote", "set-url", "origin", "https://u:t@github.com/o/r.git")
        self.escrever_lista("url:https://github.com/o/")

        decisao, motivo = self.decidir("git push origin main", self.projeto)

        self.assertEqual("allow", decisao)
        self.assertEqual("Autonomia (url:https://github.com/o/)", motivo)

    def test_worktree_de_projeto_da_lista_recebe_allow(self):
        worktree = self.temp / "wt"
        self.git(self.projeto, "worktree", "add", "-q", "-b", "outra", str(worktree))

        decisao, _ = self.decidir("git push origin outra", worktree)

        self.assertEqual("allow", decisao)

    def test_fora_da_lista_continua_ask(self):
        decisao, _ = self.decidir("git push --force origin main", self.outro)

        self.assertEqual("ask", decisao)

    def test_comando_que_muda_de_repo_pede_aprovacao(self):
        for comando in ("cd " + str(self.outro) + " && git push", "git -C " + str(self.outro) + " push",
                        "gh pr merge 3 -R o/r", "pushd " + str(self.outro) + "; git push",
                        "bash -c 'cd " + str(self.outro) + " && git push'", "GIT_DIR=/x/.git git push",
                        "git -c remote.origin.url=https://github.com/x/y.git push origin main",
                        "git push https://github.com/x/y.git main", "gh repo delete x/y",
                        "gh api -X DELETE repos/x/y", "gh pr merge https://github.com/x/y/pull/3",
                        "gh pr merge https://github.com/o/r/../../x/y/pull/3",
                        "git remote set-url origin https://github.com/x/y.git && git push origin main",
                        "git remote add outro https://github.com/x/y.git; git push outro main",
                        "git config remote.origin.pushurl https://github.com/x/y.git && git push",
                        "echo '[url \"https://github.com/x/\"]' >> ~/.gitconfig && git push",
                        "printf x >> .git/conf'ig' && git push origin main"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_alvo_explicito_do_proprio_repo_recebe_allow(self):
        for comando in ("gh pr merge https://github.com/o/r/pull/3", "git push", "git push -u origin feature",
                        "git push origin do", "git push origin then",
                        "git push --force-with-lease origin main", "gh repo delete o/r --yes",
                        "gh pr create --head feature/x --base release/1 --title 'a/b' --body 'c/d'"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_push_para_destino_que_nao_e_remoto_nomeado_pede_aprovacao(self):
        (self.projeto / "outro").mkdir()
        for lista in ("pasta:" + str(self.projeto), "url:https://github.com/o/"):
            self.escrever_lista(lista)
            for comando in ("git push https://github.com/o/r.git main", "git push ../outro main",
                            "git push /tmp/x main", "git push outro main", "git push -f -- ../outro main",
                            "git push do main", "git push if main", "git push then main", "git push else main",
                            "git push while main", "git push '!' main", "git push \"do\" main",
                            "git push '(' main", "git push \"{\" main", "git push \"!\" main",
                            "git push \";\" main", "git push -o x do main", "git push d'o' main",
                            "ln -s ../outro do && git push do main"):
                decisao, _ = self.decidir(comando, self.projeto)
                self.assertEqual("ask", decisao, (lista, comando))

    def test_owner_repo_onde_o_gh_espera_repo_pede_aprovacao(self):
        for comando in ("gh issue transfer 3 x/y", "gh repo edit x/y --description z",
                        "gh pr merge https://github.com/x/y/pull/3", "gh pr close git@github.com:x/y.git",
                        "gh pr close https://github.com/x/y/pull/3 --comment a/b"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_branch_tag_ou_caminho_com_barra_no_gh_recebe_allow(self):
        for comando in ("gh pr merge feature/x", "gh pr merge feature/x --squash --delete-branch",
                        "gh pr close fix/y", "gh pr ready qa/z", "gh release upload v1 dist/a.zip",
                        "gh issue transfer 3 o/r"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_dados_com_tipo_errado_saem_em_silencio(self):
        for entrada in ([], {"tool_name": "Bash", "tool_input": "git push"},
                        {"tool_name": "Bash", "tool_input": {"command": 5}}, {"tool_name": "Bash", "tool_input": []}):
            processo = subprocess.run([sys.executable, "-I", str(GUARDA)], input=json.dumps(entrada).encode(),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
            self.assertEqual((0, b"", b""), (processo.returncode, processo.stdout, processo.stderr), entrada)

    def test_cwd_invalido_pede_aprovacao(self):
        fora_de_repo = self.temp / "solta"
        fora_de_repo.mkdir()
        for cwd in (None, str(self.temp / "nao-existe"), str(fora_de_repo)):
            entrada = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
            if cwd is not None:
                entrada["cwd"] = cwd
            saida = GitGuardTest.rodar_guarda(entrada, cwd=self.projeto, env=self.env)
            self.assertEqual("ask", json.loads(saida)["hookSpecificOutput"]["permissionDecision"], (cwd, saida))

    def test_citar_a_lista_pede_aprovacao(self):
        for comando in ("echo x >> ~/.config/git/autonomia.lista", "cat autonomia.lista"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_rodar_autonomia_adicionar_pede_aprovacao(self):
        for comando in ("~/.config/git/autonomia adicionar", "autonomia adicionar pasta:/x",
                        "bash -c 'autonomia adicionar'", "script -q /dev/null autonomia adicionar"):
            for cwd in (self.projeto, self.outro):
                decisao, _ = self.decidir(comando, cwd)
                self.assertEqual("ask", decisao, comando)

    def test_lista_citada_com_aspas_ou_barra_pede_aprovacao(self):
        for comando in ("printf 'pasta:/x\\n' >> ~/.config/git/autonomia.li'sta'", "cat \"autonomia\".lista",
                        "cp x ~/.config/git/autonomia\\.lista", "ls ~/.config/git/autonomia.l*",
                        "cat ~/.config/git/autonomia?lista", "cat ~/.config/git/autonomia[.]lista",
                        "cat ~/.config/git/autonomia*",
                        "auto\"\"nomia adicionar", "'autonomia'   'adicionar' pasta:/x"):
            decisao, _ = self.decidir(comando, self.outro)
            self.assertEqual("ask", decisao, comando)

    def test_variavel_ou_substituicao_em_git_ou_gh_pede_aprovacao(self):
        self.escrever_lista("url:https://github.com/o/")
        for comando in ("U=https://github.com/x/y.git; git push $U main", "R=x/y; gh repo delete $R --yes",
                        "git push $(cat f) main", "git push `cat f` main", "git push \"${U}\" main",
                        "git push $(echo evil) main", "git push `echo evil` main",
                        "if true; then git push $U main; fi", "R=x/y; if true; then gh repo delete $R --yes; fi",
                        "( git push $U main )", "{ git push $U main; }", "! git push $U main",
                        "for x in a; do git push $U main; done", "while true; do git push $U main; break; done",
                        "echo $(git push $U main)", "echo `git push $U main`", "$(git push $U main)",
                        "if true; then git push origin `echo main`; fi", "x$(git push $U main)",
                        "if git push $U main; then :; fi", "until git push $U main; do :; done",
                        "echo x/y | xargs gh repo delete --yes", "bash -c 'git push $U main'"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_substituicao_em_segmento_sem_motivo_nao_tira_a_autonomia(self):
        self.escrever_lista("url:https://github.com/o/")
        for comando in ("git commit -m \"$(cat m)\" && git push origin main",
                        "git commit -m `cat m` && git push origin main"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_substituicao_citada_ou_nao_e_analisada_como_comando(self):
        (self.projeto / "outro").mkdir()
        for comando in ("git commit -m \"$(git push ../outro HEAD:refs/heads/vazou)\" && git push origin b",
                        "git commit -m \"$(cd /x && git push)\" && git push origin b",
                        "git commit -m \"$(gh repo delete x/y)\"", "git commit -m \"`git push ../outro`\"",
                        "git commit -m \"x $(echo \"$(git push ../outro main)\")\" && git push origin b",
                        "git commit -m \"$(case a in *) git push ../outro;; esac)\" && git push origin b",
                        "cat <<E\n$(git push ../outro main)\nE\ngit push origin b",
                        "diff <(git push ../outro main) x", "U=x; git push $U main",
                        "git push \"$(echo evil)\" main", "git push origin \"$(echo main)\""):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)
        for comando in ("git commit -m \"$(git push ../outro HEAD:refs/heads/vazou)\" && git push origin b",
                        "git commit -m \"$(git push ../outro main)\"", "echo \"`gh repo delete x/y`\""):
            decisao, _ = self.decidir(comando, self.outro)
            self.assertEqual("ask", decisao, comando)

    def test_dois_menores_fora_de_here_doc_nao_escondem_as_linhas_seguintes(self):
        for comando in ("x=ab; echo ${x/<</}\ngit push ../outro main",
                        "x=ab; echo ${x/<</}\ngit push ../outro main\n/}",
                        "echo $[1<<2]\ngit push ../outro main", "echo $((1<<2))\ngit push ../outro main",
                        "((x=1<<2))\ngit push ../outro main", "a[1<<2]=3\ngit push ../outro main",
                        "echo ${a[1<<2]}\ngh repo delete x/y --yes", "cat <<FIM\nx\ngit push ../outro main"):
            for cwd in (self.projeto, self.outro):
                decisao, _ = self.decidir(comando, cwd)
                self.assertEqual("ask", decisao, (comando, cwd.name))
        for comando in ("echo ${HOME} $[1+2] ${a[1<<2]}\ngit push origin b",
                        "a[1<<2]=3; echo ${x:-\"}\"}\ngit push origin b"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_comandos_comuns_do_agente_recebem_allow(self):
        for comando in ("git add -A && git commit -m \"$(cat <<'EOF'\nAs Is: x\nTo Be: y\nEOF\n)\" && "
                        "git push origin minha-branch",
                        "gh pr create --title \"t\" --body \"$(cat <<'EOF'\ncorpo\nEOF\n)\" --base main --head feature/x",
                        "gh pr create --title=\"$(cat t)\" --body=\"$(cat b)\"",
                        "git commit -m \"$(cat m)\" && git push origin main",
                        "gh pr merge feature/x --squash --delete-branch",
                        "git commit -m \"$(cat <<'EOF'\nAs Is: diz \"oi\nEOF\n)\" && git push origin b",
                        "git commit -F - <<'EOF'\nAs Is: it's\nEOF\ngit push origin b"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_substituicao_fora_de_valor_de_texto_citado_tira_a_autonomia(self):
        for comando in ("gh pr create --title t --body $(cat f)", "gh pr create --body $U",
                        "gh pr create --body x$U", "gh pr merge \"$(cat f)\"", "gh pr create --head \"$(cat f)\"",
                        "gh pr merge --subject s \"$(cat f)\""):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_leitura_e_here_doc_fora_da_lista_ficam_em_silencio(self):
        for comando in ("git log --oneline | head -5", "cat <<'EOF' > arq.txt\nit's \"x\nEOF",
                        "git commit -m \"$(cat <<'EOF'\nAs Is: diz \"oi\nEOF\n)\"",
                        "git commit -F - <<'EOF'\nAs Is: it's\nEOF"):
            saida = GitGuardTest.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": comando},
                                               "cwd": str(self.outro)}, cwd=self.outro, env=self.env)
            self.assertEqual("", saida, comando)

    def test_url_da_lista_exige_que_todo_remoto_case(self):
        self.escrever_lista("url:https://github.com/o/")
        self.git(self.projeto, "remote", "add", "evil", "https://github.com/x/y.git")
        for comando in ("git push evil main", "git push origin main"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_url_da_lista_confere_pushurl(self):
        self.escrever_lista("url:https://github.com/o/")
        self.git(self.projeto, "config", "remote.origin.pushurl", "https://github.com/x/y.git")

        decisao, _ = self.decidir("git push origin main", self.projeto)

        self.assertEqual("ask", decisao)

    def test_url_da_lista_usa_a_url_efetiva_com_insteadof(self):
        self.escrever_lista("url:https://github.com/o/")
        for chave in ("url.https://github.com/x/.insteadOf", "url.https://github.com/x/.pushInsteadOf"):
            self.git(self.projeto, "config", chave, "https://github.com/o/")
            decisao, _ = self.decidir("git push origin main", self.projeto)
            self.assertEqual("ask", decisao, chave)
            self.git(self.projeto, "config", "--unset", chave)

    def test_pasta_da_lista_vale_para_todo_remoto_do_repo(self):
        self.git(self.projeto, "remote", "add", "evil", "https://github.com/x/y.git")

        decisao, _ = self.decidir("git push evil main", self.projeto)

        self.assertEqual("allow", decisao)

    def test_gh_fora_do_escopo_do_repo_pede_aprovacao(self):
        for comando in ("gh extension install x/y", "gh auth refresh -s delete_repo", "gh gist create --public f",
                        "gh ssh-key add k.pub", "gh gpg-key add k.asc", "gh repo create z --public", "gh repo fork",
                        "gh secret set X --org o", "gh secret set X -o o", "gh variable set X --env prod",
                        "gh secret set X -oevil -b v", "gh variable set X -oevil -b v", "gh secret set X --org=evil",
                        "gh secret set X --env=prod", "gh secret set X -eprod", "gh secret set X --user=x",
                        "gh secret set X --user", "gh org list", "gh codespace delete --all",
                        "gh config set editor vim", "gh alias set x y", "gh api -X DELETE repos/x/y",
                        "gh api graphql -f query=x", "gh api -X POST user/repos -f name=z",
                        "gh api -X DELETE repos/o/r/../../x/y", "gh api -X DELETE repos/o/r --hostname h.com"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_gh_do_escopo_do_repo_recebe_allow(self):
        for comando in ("gh pr merge 3", "gh issue close 4", "gh release create v1", "gh run rerun 5",
                        "gh workflow run ci.yml", "gh label create x", "gh cache delete --all",
                        "gh variable set X --body y", "gh secret set X --body y", "gh repo edit --description x",
                        "gh api -X POST repos/o/r/issues -f title=x", "gh api -X DELETE /repos/o/r/git/refs/heads/x",
                        "gh api -X POST repos/{owner}/{repo}/issues -f title=x"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_owner_repo_sem_host_so_casa_com_github(self):
        self.git(self.projeto, "remote", "set-url", "origin", "https://gitlab.com/o/r.git")

        decisao, _ = self.decidir("gh repo delete o/r --yes", self.projeto)

        self.assertEqual("ask", decisao)

    def test_comentario_termina_na_quebra_de_linha(self):
        for comando in ("# x\ngit push ../outro main", "echo a # c\ngh repo delete x/y --yes",
                        "a[1]=3 git push ../outro main", "a[1 + 1]=3 git push ../outro main",
                        "a[$(true)]=3 git push ../outro main"):
            for cwd in (self.outro, self.projeto):
                decisao, _ = self.decidir(comando, cwd)
                self.assertEqual("ask", decisao, (comando, cwd))

        decisao, _ = self.decidir("# comentário\ngit push origin b", self.projeto)
        self.assertEqual("allow", decisao)

    def test_substituicao_depois_de_comentario_tira_a_autonomia(self):
        for comando in ("# c\ngh repo delete $R --yes", "R=x/y; # c\ngh repo delete ${R} --yes",
                        "# c\ngit push $(echo ../outro) main", "# c\ngit push origin `echo x`"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)

    def test_lista_so_leitura_vence_a_autonomia(self):
        for entrada in ("pasta:" + str(self.projeto), "url:https://github.com/o/"):
            self.escrever_lista_so_leitura(entrada)
            for comando in ("gh repo delete o/r --yes", "gh pr merge 3", "git push --no-verify origin main"):
                decisao, _ = self.decidir(comando, self.projeto)
                self.assertEqual("ask", decisao, (entrada, comando))
        self.escrever_lista_so_leitura("pasta:" + str(self.outro))
        decisao, _ = self.decidir("gh pr merge 3", self.projeto)
        self.assertEqual("allow", decisao)

    def test_repo_sem_remoto_em_pasta_da_lista_nao_vira_so_leitura_por_url_de_outro_host(self):
        sem_remoto = self.temp / "sem-remoto"
        sem_remoto.mkdir()
        self.git(sem_remoto, "init", "-q")
        self.escrever_lista("pasta:" + str(sem_remoto))
        self.escrever_lista_so_leitura("url:https://git.outro-host.local/")
        decisao, motivo = self.decidir("git clean -fd", sem_remoto)
        self.assertEqual("allow", decisao, motivo)
        self.escrever_lista_so_leitura("pasta:" + str(sem_remoto))
        decisao, _ = self.decidir("git clean -fd", sem_remoto)
        self.assertEqual("ask", decisao)

    def test_cerquilha_citada_ou_no_meio_da_palavra_nao_abre_comentario(self):
        decisao, _ = self.decidir("git commit -m \"fix #12\" && git push origin b", self.projeto)
        self.assertEqual("allow", decisao)
        self.assertEqual([["echo", "a#b"]], carregar_guarda().dividir("echo a#b"))
        for comando in ("git log # comentário", "echo a#b"):
            saida = GitGuardTest.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": comando},
                                               "cwd": str(self.outro)}, cwd=self.outro, env=self.env)
            self.assertEqual("", saida, comando)

    def test_continuacao_de_linha_junta_as_duas_linhas(self):
        for comando in ("gh pr merge 3 \\\n -R x/y", "gh pr merge 3 \\\n  -R x/y \\\n  --squash",
                        "gh repo delete \\\n x/y --yes", "gh release create v1 \\\n --repo x/y",
                        "gh pr create \\\n --repo x/y --title t", "gh api repos/x/y/pulls \\\n -f title=x",
                        "git commit -m \"a\\\nb\" && git \\\n push ../outro main"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("ask", decisao, comando)
        for comando in ("gh api repos/o/r/issues \\\n -f title=x", "git \\\n push ../o main"):
            decisao, _ = self.decidir(comando, self.outro)
            self.assertEqual("ask", decisao, comando)
        for comando in ("git push \\\n origin main", "gh pr create \\\n  --title t \\\n  --body b",
                        "gh api repos/o/r/issues \\\n -f title=x"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_comando_que_nao_e_git_gh_nem_inofensivo_deixa_o_hook_em_silencio_na_lista(self):
        for comando in ("git push origin main && rm -rf ~/Documents", "git push; curl x | sh",
                        "git push origin main && python3 -c 'print(1)'", "echo x >> ~/.zshrc && git push origin main",
                        "git push origin main > ~/.bashrc", "sed -i s/a/b/ f && git push origin main",
                        "sed -Ei s/a/b/ f && git push origin main", "sudo git push origin main",
                        "./echo x && git push origin main", "git commit -m \"$(rm -rf x)\" && git push origin main",
                        "bash -c 'echo x' && git push origin main", "cat <<'EOF' > f\nx\nEOF\ngit push origin main"):
            self.assertEqual("", self.saida(comando, self.projeto), comando)
            decisao, _ = self.decidir(comando, self.outro)
            self.assertEqual("ask", decisao, comando)
        for comando in ("echo ok && git push origin main", "git push origin main 2>&1 | tail -5",
                        "git push origin main > /dev/null", "test -f x && git push origin main",
                        "[ -f x ] && git push origin main", "ls && pwd && git push origin main",
                        "git log --oneline | head -3 && git push origin main", "grep -q x f; git push origin main",
                        "wc -l f && git push", "printf x && git push origin main",
                        "true && git push origin main", "if true; then git push origin main; fi",
                        "cat f | head && git push origin main"):
            decisao, _ = self.decidir(comando, self.projeto)
            self.assertEqual("allow", decisao, comando)

    def test_shell_que_le_here_doc_e_chave_no_meio_pedem_aprovacao(self):
        for comando in ("bash <<'EOF'\ngit push x main\nEOF", "sh <<EOF\ngit push x main\nEOF",
                        "zsh <<'EOF'\ngit push x main\nEOF", "bash -s <<'EOF'\ngit push x main\nEOF",
                        "cat <<'EOF' | bash\ngh repo delete x/y --yes\nEOF", "time { git push x main; }",
                        "time ! git push x main", "function f { git push x main; }; f",
                        "if true { git push x main }"):
            for cwd in (self.projeto, self.outro):
                decisao, _ = self.decidir(comando, cwd)
                self.assertEqual("ask", decisao, (comando, cwd.name))
        decisao, _ = self.decidir("bash <<'EOF'\ngit push origin main\nEOF", self.projeto)
        self.assertEqual("ask", decisao)

    def test_sed_e_git_config_que_grava_deixam_o_hook_em_silencio_na_lista(self):
        for comando in ("git push origin main && sed 's/^/x/w ~/.zshrc' f", "sed -n 1p f && git push origin main",
                        "git config credential.helper '!cmd' && git push origin main",
                        "git config core.sshCommand 'x' && git push origin main",
                        "git config core.hooksPath /tmp/h && git push origin main",
                        "git config --unset user.name && git push origin main",
                        "git config set core.pager x && git push origin main",
                        "git config credential.helper '!touch /tmp/p' --get && git push origin main",
                        "git config core.sshCommand 'touch /tmp/p' -l && git push origin main",
                        "git config core.hooksPath list && git push origin main",
                        "git config set core.sshCommand get && git push origin main",
                        "git config --global core.sshCommand x && git push origin main",
                        "git config set core.sshCommand x && git push origin main",
                        "git config user.email && git push origin main",
                        "git config --get user.email && git push origin main",
                        "git config --list && git push origin main"):
            self.assertEqual("", self.saida(comando, self.projeto), comando)
            decisao, _ = self.decidir(comando, self.outro)
            self.assertEqual("ask", decisao, comando)
        for comando in ("git -c core.sshCommand=x push origin main", "git -c core.hooksPath=/tmp/h push origin main"):
            for cwd in (self.projeto, self.outro):
                decisao, _ = self.decidir(comando, cwd)
                self.assertEqual("ask", decisao, (comando, cwd.name))
        for comando in ("git config --get user.email", "git config --list", "git config user.email"):
            self.assertEqual("", self.saida(comando, self.projeto), comando)
            self.assertEqual("", self.saida(comando, self.outro), comando)

    def saida(self, comando, cwd):
        return GitGuardTest.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": comando}, "cwd": str(cwd)},
                                         cwd=cwd, env=self.env)

    def decidir(self, comando, cwd):
        saida = GitGuardTest.rodar_guarda({"tool_name": "Bash", "tool_input": {"command": comando}, "cwd": str(cwd)},
                                          cwd=cwd, env=self.env)
        self.assertTrue(saida, comando)
        dados = json.loads(saida)["hookSpecificOutput"]
        return dados["permissionDecision"], dados["permissionDecisionReason"]

    def criar_repo(self, nome, url):
        repo = self.temp / nome
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        self.git(repo, "remote", "add", "origin", url)
        return repo

    def escrever_lista(self, *entradas):
        lista = self.home / ".config" / "git" / "autonomia.lista"
        lista.parent.mkdir(parents=True, exist_ok=True)
        lista.write_text("".join("  " + entrada + "  \n" for entrada in entradas))

    def escrever_lista_so_leitura(self, *entradas):
        lista = self.home / ".config" / "git" / "somente-leitura.lista"
        lista.parent.mkdir(parents=True, exist_ok=True)
        lista.write_text("".join(entrada + "\n" for entrada in entradas))

    def git(self, repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], env=self.env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class InstaladorDoGitGuardTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="githooks-guarda-")
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name).resolve()
        self.home = self.temp / "home"
        self.home.mkdir()
        self.config = self.home / ".claude"

    def test_sem_pasta_de_configuracao_nao_instala_nada(self):
        resultado = self.instalar()

        self.assertEqual(0, resultado.returncode, resultado.stdout)
        self.assertFalse(self.config.exists())
        self.assertIn("git_guard", resultado.stdout)

    def test_instala_registra_e_preserva_o_resto_da_configuracao(self):
        self.config.mkdir()
        original = {
            "model": "x",
            "hooks": {
                "PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "outro-hook"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "ao-parar"}]}],
            },
        }
        self.settings().write_text(json.dumps(original))

        primeira = self.instalar()
        segunda = self.instalar()

        self.assertEqual(0, primeira.returncode, primeira.stdout)
        self.assertEqual(0, segunda.returncode, segunda.stdout)
        self.assertEqual(GUARDA.read_bytes(), (self.config / "hooks/git_guard.py").read_bytes())
        dados = json.loads(self.settings().read_text())
        self.assertEqual("x", dados["model"])
        self.assertEqual(original["hooks"]["Stop"], dados["hooks"]["Stop"])
        self.assertIn(original["hooks"]["PreToolUse"][0], dados["hooks"]["PreToolUse"])
        guardas = self.entradas_da_guarda(dados)
        self.assertEqual(1, len(guardas), dados)
        self.assertEqual("Bash", guardas[0]["matcher"])
        comando = guardas[0]["hooks"][0]["command"]
        self.assertIn(str(self.config / "hooks/git_guard.py").replace("\\", "/"), comando)
        self.assertEqual(1, len(list(self.config.glob("settings.json.backup.*"))))

    def test_atualiza_registro_antigo_sem_duplicar(self):
        self.config.mkdir()
        antigo = {"matcher": "Bash", "hooks": [{"type": "command",
                                                "command": "python3 -I \"$HOME/.claude/hooks/git_guard.py\""}]}
        self.settings().write_text(json.dumps({"hooks": {"PreToolUse": [antigo]}}))

        resultado = self.instalar()

        self.assertEqual(0, resultado.returncode, resultado.stdout)
        guardas = self.entradas_da_guarda(json.loads(self.settings().read_text()))
        self.assertEqual(1, len(guardas))
        self.assertIn(str(self.config / "hooks/git_guard.py").replace("\\", "/"), guardas[0]["hooks"][0]["command"])

    def test_settings_invalido_nao_e_sobrescrito(self):
        self.config.mkdir()
        self.settings().write_text("{ isto nao e json")

        resultado = self.instalar()

        self.assertEqual(0, resultado.returncode, resultado.stdout)
        self.assertEqual("{ isto nao e json", self.settings().read_text())
        self.assertIn("settings.json", resultado.stdout)

    def test_respeita_claude_config_dir(self):
        outra = self.temp / "config-alternativa"
        outra.mkdir()

        resultado = self.instalar({"CLAUDE_CONFIG_DIR": str(outra)})

        self.assertEqual(0, resultado.returncode, resultado.stdout)
        self.assertTrue((outra / "hooks/git_guard.py").is_file())
        self.assertEqual(1, len(self.entradas_da_guarda(json.loads((outra / "settings.json").read_text()))))
        self.assertFalse(self.config.exists())

    def settings(self):
        return self.config / "settings.json"

    @staticmethod
    def entradas_da_guarda(dados):
        return [entrada for entrada in dados.get("hooks", {}).get("PreToolUse", [])
                if any("git_guard.py" in h.get("command", "") for h in entrada.get("hooks", []))]

    def instalar(self, env_extra=None):
        ambiente = {chave: valor for chave, valor in os.environ.items() if chave != "CLAUDE_CONFIG_DIR"}
        global_ = self.temp / "gitconfig"
        global_.touch()
        ambiente.update({"GITHOOKS_HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"),
                         "GIT_CONFIG_GLOBAL": str(global_), "GIT_CONFIG_NOSYSTEM": "1"})
        ambiente.update(env_extra or {})
        return subprocess.run(["bash", str(PROJECT / "install.sh")], cwd=PROJECT, env=ambiente,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, input="")


if __name__ == "__main__":
    unittest.main()
