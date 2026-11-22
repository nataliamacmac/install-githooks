"""Testes de integração dos hooks globais.

Cada teste cria repositórios temporários e executa Git e Bash de verdade. A configuração Git da máquina não muda:
os processos rodam com GIT_CONFIG_NOSYSTEM, um GIT_CONFIG_GLOBAL vazio e um XDG_CONFIG_HOME vazio.

Execução, na raiz do projeto:
    python3 -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
HOOKS = PROJECT / "hooks"
TERMO = "feat: adicionar suporte a Claude Code"
OID_ZERO = "0" * 40


def achar_bash():
    bash = shutil.which("bash")
    if bash:
        return bash
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidato = Path(program_files, "Git", "bin", "bash.exe")
        if candidato.is_file():
            return str(candidato)
    return "bash"


BASH = achar_bash()


class Resultado:
    def __init__(self, codigo, saida):
        self.codigo = codigo
        self.saida = saida

    def __repr__(self):
        return f"Resultado(codigo={self.codigo}, saida={self.saida!r})"


class HookScriptsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._isolamento = tempfile.TemporaryDirectory(prefix="githooks-isolamento-")
        base = Path(cls._isolamento.name)
        cls.global_vazio = base / "gitconfig-vazio"
        cls.global_vazio.touch()
        cls.xdg_vazio = base / "xdg-vazio"
        cls.xdg_vazio.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._isolamento.cleanup()

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="githooks-teste-")
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name).resolve()

    # ---------------------------------------------------------------- mensagens, referências e pre-push

    def test_commit_no_verify_ainda_bloqueia_mensagem_proibida(self):
        repo = self.criar_repositorio("no-verify")
        head_antes = self.head(repo)

        commit = self.git(repo, "commit", "--allow-empty", "--no-verify", "-m", TERMO)

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertIn("claude", commit.saida.lower(), commit.saida)
        self.assertEqual(head_antes, self.head(repo))

    def test_recusa_nome_de_branch_proibido(self):
        repo = self.criar_repositorio("branch")

        branch = self.git(repo, "branch", "codex-feature")

        self.assertNotEqual(0, branch.codigo, branch.saida)
        self.assertFalse((repo / ".git/refs/heads/codex-feature").exists())

    def test_pre_push_inspeciona_commits_e_coautoria(self):
        repo = self.criar_repositorio("push")
        base = self.head(repo)
        tree = self.rev(repo, "HEAD^{tree}")

        for mensagem in ("feat: integrar OpenAI", "Co-authored-by: IA <ia@example.invalid>"):
            commit = self.git(repo, "commit-tree", tree, "-p", base, "-m", mensagem)
            self.assertEqual(0, commit.codigo, commit.saida)
            oid = commit.saida.strip()
            hook = self.pre_push_com(repo, f"refs/heads/feature/teste {oid} refs/heads/feature/teste {base}\n")
            self.assertNotEqual(0, hook.codigo, hook.saida)

    def test_pre_push_valida_historico_quando_oid_remoto_nao_foi_baixado(self):
        repo = self.criar_repositorio("push-remoto-novo")
        local = self.head(repo)

        hook = self.pre_push_com(repo, f"refs/heads/main {local} refs/heads/main {'1' * 40}\n")

        self.assertEqual(0, hook.codigo, hook.saida)

    def test_pre_push_nao_reconfere_commit_que_ja_esta_no_remoto(self):
        repo = self.criar_repositorio("push-ja-no-remoto")
        base = self.head(repo)
        tree = self.rev(repo, "HEAD^{tree}")
        # commit antigo com o termo, que já foi enviado a outra branch do mesmo remoto
        antigo = self.git(repo, "commit-tree", tree, "-p", base, "-m", TERMO).saida.strip()
        self.assertEqual(0, self.git(repo, "update-ref", "refs/remotes/origin/desenvolvimento", antigo).codigo)

        hook = self.pre_push_com(repo, f"refs/heads/main {antigo} refs/heads/main {base}\n")

        self.assertEqual(0, hook.codigo, hook.saida)

    def test_pre_push_bloqueia_commit_novo_com_termo_mesmo_com_historico_ja_enviado(self):
        repo = self.criar_repositorio("push-novo-com-termo")
        base = self.head(repo)
        tree = self.rev(repo, "HEAD^{tree}")
        antigo = self.git(repo, "commit-tree", tree, "-p", base, "-m", "feat: ajuste antigo").saida.strip()
        self.assertEqual(0, self.git(repo, "update-ref", "refs/remotes/origin/desenvolvimento", antigo).codigo)
        novo = self.git(repo, "commit-tree", tree, "-p", antigo, "-m", TERMO).saida.strip()

        hook = self.pre_push_com(repo, f"refs/heads/main {novo} refs/heads/main {base}\n")

        self.assertNotEqual(0, hook.codigo, hook.saida)

    def test_pre_push_bloqueia_termo_ja_enviado_so_para_outro_remoto(self):
        repo = self.criar_repositorio("push-outro-remoto")
        base = self.head(repo)
        tree = self.rev(repo, "HEAD^{tree}")
        antigo = self.git(repo, "commit-tree", tree, "-p", base, "-m", TERMO).saida.strip()
        # o commit está só no remoto "origin"; o push agora vai para o remoto "interno"
        self.assertEqual(0, self.git(repo, "update-ref", "refs/remotes/origin/desenvolvimento", antigo).codigo)

        hook = self.pre_push_com(repo, f"refs/heads/main {antigo} refs/heads/main {base}\n",
                                 "interno", "https://example.invalid/outro")

        self.assertNotEqual(0, hook.codigo, hook.saida)

    def test_amend_proprio_com_termo_e_bloqueado_mesmo_com_no_verify(self):
        repo = self.criar_repositorio("amend")
        head_antes = self.head(repo)

        amend = self.git(repo, "commit", "--amend", "--allow-empty", "--no-verify", "-m", TERMO)

        self.assertNotEqual(0, amend.codigo, amend.saida)
        self.assertEqual(head_antes, self.head(repo))

    def test_trocar_email_do_committer_por_variavel_nao_desvia_bloqueio(self):
        repo = self.criar_repositorio("committer-env")
        head_antes = self.head(repo)

        commit = self.git_com_env(repo, {"GIT_COMMITTER_EMAIL": "outro@example.invalid"},
                                  "commit", "--allow-empty", "--no-verify", "-m", TERMO)

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertEqual(head_antes, self.head(repo))

    def test_commit_proprio_sem_termo_e_permitido(self):
        repo = self.criar_repositorio("limpo")

        commit = self.git(repo, "commit", "--allow-empty", "--no-verify", "-m", "feat: ajustar validacao")

        self.assertEqual(0, commit.codigo, commit.saida)

    def test_clone_fetch_e_pull_de_commit_de_terceiro_com_termo_sao_permitidos(self):
        origem = self.criar_repositorio_de_terceiro("origem-clone")

        clone = self.clonar(origem, "clone")
        self.assertEqual(self.rev(origem, "main"), self.head(clone))

        self.commit_de_terceiro(origem, "feat: integrar OpenAI")
        fetch = self.git(clone, "fetch", "origin")
        self.assertEqual(0, fetch.codigo, fetch.saida)
        pull = self.git(clone, "pull", "--ff-only")
        self.assertEqual(0, pull.codigo, pull.saida)
        self.assertEqual(self.rev(origem, "main"), self.head(clone))

    def test_branch_local_a_partir_de_commit_de_terceiro_e_permitida(self):
        clone = self.clonar(self.criar_repositorio_de_terceiro("origem-branch"), "clone-branch")

        checkout = self.git(clone, "checkout", "-b", "x", "origin/main")

        self.assertEqual(0, checkout.codigo, checkout.saida)

    def test_cherry_pick_de_commit_de_terceiro_com_termo_e_bloqueado(self):
        clone = self.clonar(self.criar_repositorio_de_terceiro("origem-pick"), "clone-pick")
        self.assertEqual(0, self.git(clone, "checkout", "-b", "base", "origin/main~1").codigo)
        self.assertEqual(0, self.git(clone, "commit", "--allow-empty", "-m", "feat: base propria").codigo)
        head_antes = self.head(clone)

        pick = self.git(clone, "cherry-pick", "--allow-empty", "origin/main")

        self.assertNotEqual(0, pick.codigo, pick.saida)
        self.assertEqual(head_antes, self.rev(clone, "refs/heads/base"))

    def test_rebase_e_amend_de_commit_de_terceiro_com_termo_sao_bloqueados(self):
        clone = self.clonar(self.criar_repositorio_de_terceiro("origem-rebase"), "clone-rebase")
        self.assertEqual(0, self.git(clone, "checkout", "-b", "base", "origin/main~1").codigo)
        self.assertEqual(0, self.git(clone, "commit", "--allow-empty", "-m", "feat: base propria").codigo)
        self.assertEqual(0, self.git(clone, "checkout", "-b", "x", "origin/main").codigo)
        terceiro = self.head(clone)

        rebase = self.git(clone, "rebase", "--keep-empty", "--onto", "base", "origin/main~1", "x")
        self.assertNotEqual(0, rebase.codigo, rebase.saida)
        self.assertEqual(terceiro, self.rev(clone, "refs/heads/x"))
        self.git(clone, "rebase", "--abort")

        amend = self.git(clone, "commit", "--amend", "--allow-empty", "--no-edit", "--no-verify")
        self.assertNotEqual(0, amend.codigo, amend.saida)
        self.assertEqual(terceiro, self.rev(clone, "refs/heads/x"))

    def test_sem_identidade_determinavel_valida_tudo(self):
        origem = self.criar_repositorio_de_terceiro("origem-sem-identidade")
        repo = self.temp / "sem-identidade"
        repo.mkdir()
        self.assertEqual(0, self.git(repo, "init", "-b", "main").codigo)
        self.git(repo, "config", "core.hooksPath", self.hooks_path())
        self.git(repo, "config", "user.useConfigOnly", "true")
        self.git(repo, "remote", "add", "origin", str(origem))

        fetch = self.git(repo, "fetch", "origin")
        self.assertEqual(0, fetch.codigo, fetch.saida)
        branch = self.git(repo, "branch", "y", "origin/main")
        self.assertNotEqual(0, branch.codigo, branch.saida)

    def test_sem_user_email_usa_identidade_efetiva_do_git(self):
        origem = self.criar_repositorio_de_terceiro("origem-identidade-efetiva")
        repo = self.temp / "identidade-efetiva"
        repo.mkdir()
        env = {"EMAIL": "teste@example.invalid"}
        self.assertEqual(0, self.git(repo, "init", "-b", "main").codigo)
        self.git(repo, "config", "core.hooksPath", self.hooks_path())
        self.git(repo, "config", "user.name", "Teste Local")
        self.git(repo, "remote", "add", "origin", str(origem))

        self.assertEqual(0, self.git_com_env(repo, env, "fetch", "origin").codigo)
        branch = self.git_com_env(repo, env, "checkout", "-b", "y", "origin/main")
        self.assertEqual(0, branch.codigo, branch.saida)
        commit = self.git_com_env(repo, env, "commit", "--allow-empty", "--no-verify", "-m", TERMO)
        self.assertNotEqual(0, commit.codigo, commit.saida)

    def test_nome_proibido_so_em_refs_remotes_e_permitido(self):
        clone = self.clonar(self.criar_repositorio_de_terceiro("origem-nome"), "clone-nome")

        self.assertEqual(0, self.git(clone, "rev-parse", "--verify", "refs/remotes/origin/codex-feature").codigo)
        branch = self.git(clone, "branch", "codex-feature", "origin/codex-feature")
        self.assertNotEqual(0, branch.codigo, branch.saida)

    def test_pre_push_ignora_historico_de_terceiro_em_branch_nova(self):
        clone = self.clonar(self.criar_repositorio_de_terceiro("origem-push"), "clone-push")
        self.assertEqual(0, self.git(clone, "checkout", "-b", "nova", "origin/main").codigo)
        self.assertEqual(0, self.git(clone, "commit", "--allow-empty", "-m", "feat: ajuste proprio").codigo)
        limpo = self.head(clone)

        permitido = self.pre_push(clone, limpo)
        self.assertEqual(0, permitido.codigo, permitido.saida)

        tree = self.rev(clone, "HEAD^{tree}")
        proibido = self.git(clone, "commit-tree", tree, "-p", limpo, "-m", TERMO).saida.strip()
        bloqueado = self.pre_push(clone, proibido)
        self.assertNotEqual(0, bloqueado.codigo, bloqueado.saida)

    def test_scanner_avisa_sobre_core_hooks_path_local(self):
        raiz = self.temp / "repos"
        custom = raiz / "repo-customizado"
        custom.mkdir(parents=True)
        self.git(custom, "init")
        self.git(custom, "config", "--local", "core.hooksPath", "./hooks-especiais")

        scan = self.executar(raiz, None, {}, BASH, str(PROJECT / "scripts/scan-custom-hooks.sh"), str(raiz))

        self.assertEqual(0, scan.codigo, scan.saida)
        self.assertIn("repo-customizado", scan.saida)
        self.assertIn("substitui os hooks globais", scan.saida)

    def test_mensagem_com_nomes_de_modelo_e_subagente_e_bloqueada(self):
        repo = self.criar_repositorio("modelos")
        head_antes = self.head(repo)

        for mensagem in ("feat: validado por Opus", "fix: executado pelo Sonnet", "docs: revisao Haiku",
                         "chore: tarefa do subagente", "chore: sub-agent concluiu", "chore: subagentes em paralelo"):
            commit = self.git(repo, "commit", "--allow-empty", "-m", mensagem)
            self.assertNotEqual(0, commit.codigo, f"{mensagem} -> {commit.saida}")
        self.assertEqual(head_antes, self.head(repo))

    # ---------------------------------------------------------------- pre-commit: nome e conteúdo

    def test_pre_commit_bloqueia_nome_de_arquivo_com_termo_de_ia(self):
        repo = self.criar_repositorio("pc-nome")
        (repo / "relatorio-opus.md").write_text("conteudo neutro\n")
        self.assertEqual(0, self.git(repo, "add", "relatorio-opus.md").codigo)
        head_antes = self.head(repo)

        commit = self.git(repo, "commit", "-m", "docs: relatorio")

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertIn("nome de arquivo", commit.saida)
        self.assertEqual(head_antes, self.head(repo))

    def test_pre_commit_bloqueia_conteudo_com_termo_de_ia(self):
        repo = self.criar_repositorio("pc-conteudo")
        for linha in ("Validado pelo Opus.", "executor: Sonnet", "revisao do Haiku", "feito por subagentes",
                      "Gerado com Claude Code", "usa o OpenAI", "modelo gpt-4o", "via Copilot"):
            (repo / "nota.md").write_text(linha + "\n")
            self.assertEqual(0, self.git(repo, "add", "nota.md").codigo)

            commit = self.git(repo, "commit", "-m", "docs: nota")

            self.assertNotEqual(0, commit.codigo, f"{linha} -> {commit.saida}")
            self.assertIn("conteudo adicionado em nota.md", commit.saida)
            self.assertEqual(0, self.git(repo, "reset", "-q").codigo)

    def test_pre_commit_permite_conteudo_e_linhas_removidas_sem_termo(self):
        repo = self.criar_repositorio("pc-limpo")
        (repo / "Consulta.java").write_text(
            "// cursor do banco, codex de leis e gemini do zodiaco; GPT e a tabela de particoes\n")
        self.assertEqual(0, self.git(repo, "add", "Consulta.java").codigo)

        commit = self.git(repo, "commit", "-m", "feat: consulta")

        self.assertEqual(0, commit.codigo, commit.saida)

    def test_pre_commit_permite_uso_tecnico_de_termo_ambiguo_em_codigo(self):
        repo = self.criar_repositorio("pc-codigo")
        arquivos = {
            "estilo.scss": ".botao {\n  cursor: pointer;\n}\n",
            "Lista.vue": "<template><div style=\"cursor: pointer\">Haiku</div></template>\n",
            "consulta.sql": "DECLARE c CURSOR FOR SELECT opus, sonnet FROM obras;\n",
            "pagina.html": "<p>Sonnet 18 e o codex de leis</p>\n",
            "cursor.ts": "export const cursor = 1;\n",
        }
        for nome, conteudo in arquivos.items():
            (repo / nome).write_text(conteudo)
            self.assertEqual(0, self.git(repo, "add", nome).codigo)

        commit = self.git(repo, "commit", "-m", "feat: tela")

        self.assertEqual(0, commit.codigo, commit.saida)

    def test_pre_commit_bloqueia_termo_ambiguo_em_texto_de_prosa(self):
        repo = self.criar_repositorio("pc-prosa")
        for nome, conteudo in (("nota.md", "Feito no editor Cursor.\n"), ("leia.txt", "revisado com o Gemini\n"),
                               ("guia.adoc", "rodado no Codex\n"), ("notas-cursor.md", "conteudo neutro\n")):
            (repo / nome).write_text(conteudo)
            self.assertEqual(0, self.git(repo, "add", nome).codigo)

            commit = self.git(repo, "commit", "-m", "docs: nota")

            self.assertNotEqual(0, commit.codigo, f"{nome} -> {commit.saida}")
            self.assertEqual(0, self.git(repo, "reset", "-q").codigo)
            (repo / nome).unlink()

    def test_pre_commit_bloqueia_termo_forte_mesmo_em_codigo(self):
        repo = self.criar_repositorio("pc-forte")
        for nome, conteudo in (("estilo.scss", "/* gerado pelo Copilot */\n"),
                               ("consulta.sql", "-- escrito com Claude Code\n"),
                               ("Lista.vue", "<!-- feito por subagentes -->\n")):
            (repo / nome).write_text(conteudo)
            self.assertEqual(0, self.git(repo, "add", nome).codigo)

            commit = self.git(repo, "commit", "-m", "feat: tela")

            self.assertNotEqual(0, commit.codigo, f"{nome} -> {commit.saida}")
            self.assertIn("conteudo adicionado em " + nome, commit.saida)
            self.assertEqual(0, self.git(repo, "reset", "-q").codigo)
            (repo / nome).unlink()

    def test_pre_commit_so_ve_linhas_adicionadas_nao_as_removidas(self):
        repo = self.criar_repositorio("pc-remocao")
        (repo / "nota.md").write_text("linha antiga\n")
        self.assertEqual(0, self.git(repo, "add", "nota.md").codigo)
        self.assertEqual(0, self.git(repo, "commit", "-m", "docs: nota").codigo)
        # simula um termo já existente no histórico e sendo removido agora
        (repo / "nota.md").write_text("linha nova limpa\n")
        self.assertEqual(0, self.git(repo, "add", "nota.md").codigo)

        commit = self.git(repo, "commit", "-m", "docs: limpar nota")

        self.assertEqual(0, commit.codigo, commit.saida)

    def test_pre_commit_permite_mover_arquivo_que_ja_tinha_o_termo_no_historico(self):
        repo = self.criar_repositorio("pc-mover")
        (repo / "config.json").write_text("{\n  \"nome\": \"copilot-teste\",\n  \"x\": 1\n}\n")
        self.assertEqual(0, self.git(repo, "add", "config.json").codigo)
        # o termo já está no histórico: entrou antes do hook existir
        self.assertEqual(0, self.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-m", "docs: config").codigo)
        (repo / "novo").mkdir()
        self.assertEqual(0, self.git(repo, "mv", "config.json", "novo/config.json").codigo)

        commit = self.git(repo, "commit", "-m", "chore: move a config")

        self.assertEqual(0, commit.codigo, commit.saida)

    def test_pre_commit_bloqueia_termo_novo_em_arquivo_movido(self):
        repo = self.criar_repositorio("pc-mover-novo")
        base = ("{\n  \"nome\": \"copilot-teste\",\n  \"a\": 1,\n  \"b\": 2,\n  \"c\": 3,\n  \"d\": 4,\n"
                "  \"e\": 5,\n  \"f\": 6,\n  \"g\": 7,\n  \"h\": 8\n")
        (repo / "config.json").write_text(base + "}\n")
        self.assertEqual(0, self.git(repo, "add", "config.json").codigo)
        self.assertEqual(0, self.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-m", "docs: config").codigo)
        (repo / "novo").mkdir()
        self.assertEqual(0, self.git(repo, "mv", "config.json", "novo/config.json").codigo)
        (repo / "novo/config.json").write_text(base.replace("\"h\": 8", "\"h\": \"Claude Code\"") + "}\n")
        self.assertEqual(0, self.git(repo, "add", "novo/config.json").codigo)

        commit = self.git(repo, "commit", "-m", "chore: move e edita a config")

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertIn("Claude Code", commit.saida)
        self.assertNotIn("copilot-teste", commit.saida)

    def test_pre_commit_respeita_caminhos_isentos_do_repositorio(self):
        repo = self.criar_repositorio("pc-isento")
        self.assertEqual(0, self.git(repo, "config", "--local", "--add", "hooks.termosIaIgnorar",
                                     "hooks/_bloquear_*").codigo)
        (repo / "hooks").mkdir()
        (repo / "hooks/_bloquear_exemplo").write_text("grep -E 'opus|sonnet'\n")
        (repo / "outro.txt").write_text("Sonnet\n")
        self.assertEqual(0, self.git(repo, "add", "hooks/_bloquear_exemplo").codigo)

        isento = self.git(repo, "commit", "-m", "chore: hook")
        self.assertEqual(0, isento.codigo, isento.saida)

        self.assertEqual(0, self.git(repo, "add", "outro.txt").codigo)
        bloqueado = self.git(repo, "commit", "-m", "chore: outro")
        self.assertNotEqual(0, bloqueado.codigo, bloqueado.saida)

    # ---------------------------------------------------------------- instalador

    def test_instalador_e_idempotente_e_configura_hooks_globais_isolados(self):
        fake_home = self.temp / "home"
        xdg_home = fake_home / "config"
        xdg_home.mkdir(parents=True)
        env = {"GITHOOKS_HOME": str(fake_home), "XDG_CONFIG_HOME": str(xdg_home),
               "GIT_CONFIG_GLOBAL": str(self.temp / "gitconfig")}

        primeira = self.executar(PROJECT, None, env, BASH, str(PROJECT / "install.sh"))
        self.assertEqual(0, primeira.codigo, primeira.saida)
        segunda = self.executar(PROJECT, None, env, BASH, str(PROJECT / "install.sh"))
        self.assertEqual(0, segunda.codigo, segunda.saida)

        hooks_path = Path(self.executar(PROJECT, None, env, "git", "config", "--global", "--get",
                                        "core.hooksPath").saida.strip())
        self.assertTrue((hooks_path / "reference-transaction").is_file())
        self.assertTrue((hooks_path.parent / "scan-custom-hooks.sh").is_file())
        self.assertFalse(any("backup" in p.name for p in hooks_path.iterdir()))

    # ---------------------------------------------------------------- repos só leitura

    def test_sem_lista_somente_leitura_push_continua_permitido(self):
        origem = self.criar_repositorio_bare("origem-livre")
        repo = self.clonar(origem, "livre")
        env = self.configurar_lista()
        self.assertEqual(0, self.git_com_env(repo, env, "commit", "--allow-empty", "-m", "commit livre").codigo)

        push = self.git_com_env(repo, env, "push", "origin", "main")

        self.assertEqual(0, push.codigo, push.saida)

    def test_push_para_url_da_lista_e_recusado_pelo_hook(self):
        origem = self.criar_repositorio_bare("origem-trava-url")
        repo = self.clonar(origem, "trava-url")
        env = self.configurar_lista(f"url:{origem}")

        push = self.git_com_env(repo, env, "push", "origin", "HEAD:refs/heads/nova")

        self.assertNotEqual(0, push.codigo, push.saida)
        self.assertIn("só leitura", push.saida)
        self.assertNotEqual(0, self.git(origem, "rev-parse", "--verify", "refs/heads/nova").codigo)

    def test_push_instead_of_bloqueia_envio_mesmo_sem_hooks_e_preserva_fetch(self):
        origem = self.criar_repositorio_bare("origem-insteadof")
        repo = self.clonar(origem, "insteadof")
        env = self.configurar_lista(f"url:{origem}")
        aplicar = self.somente_leitura(repo, env, "aplicar")
        self.assertEqual(0, aplicar.codigo, aplicar.saida)

        push_url = self.git_com_env(repo, env, "remote", "get-url", "--push", "origin").saida.strip()
        push = self.git_com_env(repo, env, "-c", "core.hooksPath=/dev/null", "push", "--no-verify", "origin",
                                "HEAD:refs/heads/nova")
        fetch = self.git_com_env(repo, env, "fetch", "origin")

        self.assertTrue(push_url.startswith("somente-leitura://bloqueado/"), push_url)
        self.assertNotEqual(0, push.codigo, push.saida)
        self.assertEqual(0, fetch.codigo, fetch.saida)

    def test_repo_em_pasta_da_lista_nao_faz_commit_nem_push(self):
        origem = self.criar_repositorio_bare("origem-pasta")
        repo = self.clonar(origem, "pasta-travada")
        env = self.configurar_lista(f"pasta:{repo.resolve()}")
        head_antes = self.head(repo)

        commit = self.git_com_env(repo, env, "commit", "--allow-empty", "--no-verify", "-m", "commit local")
        push = self.git_com_env(repo, env, "push", "origin", "HEAD:refs/heads/nova")

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertIn("só leitura", commit.saida)
        self.assertEqual(head_antes, self.head(repo))
        self.assertNotEqual(0, push.codigo, push.saida)
        self.assertNotEqual(0, self.git(origem, "rev-parse", "--verify", "refs/heads/nova").codigo)

    def test_repo_travado_recusa_stash_reset_e_apagar_branch(self):
        origem = self.criar_repositorio_bare("origem-destrutivo")
        repo = self.clonar(origem, "destrutivo")
        env = self.configurar_lista(f"url:{origem}")
        head_antes = self.head(repo)
        (repo / "rascunho.txt").write_text("rascunho\n")
        self.assertEqual(0, self.git_com_env(repo, env, "add", "rascunho.txt").codigo)

        stash = self.git_com_env(repo, env, "stash")
        reset = self.git_com_env(repo, env, "reset", "--soft", "HEAD~1")
        apagar = self.git_com_env(repo, env, "update-ref", "-d", "refs/heads/main")

        self.assertNotEqual(0, stash.codigo, stash.saida)
        self.assertNotEqual(0, reset.codigo, reset.saida)
        self.assertNotEqual(0, apagar.codigo, apagar.saida)
        self.assertEqual(head_antes, self.head(repo))

    def test_repo_travado_recebe_fetch_pull_tag_e_clone(self):
        origem_trabalho = self.criar_repositorio("origem-trabalho")
        origem = self.temp / "origem-recebe.git"
        self.assertEqual(0, self.executar(self.temp, None, {}, "git", "clone", "--bare",
                                          str(origem_trabalho), str(origem)).codigo)
        env = self.configurar_lista(f"url:{origem}")
        repo = self.temp / "recebe"
        clone = self.executar(self.temp, None, env, "git", "clone", "-c", "core.hooksPath=" + self.hooks_path(),
                              str(origem), str(repo))
        self.assertEqual(0, clone.codigo, clone.saida)

        self.assertEqual(0, self.git(origem_trabalho, "commit", "--allow-empty", "-m", "commit novo no remoto").codigo)
        self.assertEqual(0, self.git(origem_trabalho, "tag", "v1").codigo)
        self.assertEqual(0, self.git(origem_trabalho, "push", str(origem), "main", "v1").codigo)

        pull = self.git_com_env(repo, env, "pull", "--ff-only", "origin", "main")
        tags = self.git_com_env(repo, env, "fetch", "--tags", "origin")
        branch = self.git_com_env(repo, env, "switch", "-c", "leitura", "origin/main")

        self.assertEqual(0, pull.codigo, pull.saida)
        self.assertEqual(0, tags.codigo, tags.saida)
        self.assertEqual(0, branch.codigo, branch.saida)
        self.assertEqual(self.head(origem_trabalho), self.head(repo))

    def test_worktree_de_repo_travado_nao_faz_commit_nem_push(self):
        origem = self.criar_repositorio_bare("origem-worktree")
        repo = self.clonar(origem, "principal-travado")
        env = self.configurar_lista(f"pasta:{repo.resolve()}")
        worktree = self.temp / "wt-travado"
        add = self.git_com_env(repo, env, "worktree", "add", "--detach", str(worktree))
        self.assertEqual(0, add.codigo, add.saida)
        head_antes = self.head(worktree)

        commit = self.git_com_env(worktree, env, "commit", "--allow-empty", "--no-verify", "-m", "commit no worktree")
        push = self.git_com_env(worktree, env, "push", "origin", "HEAD:refs/heads/nova")

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertEqual(head_antes, self.head(worktree))
        self.assertNotEqual(0, push.codigo, push.saida)

    def test_adicionar_sem_argumento_grava_url_do_origin_sem_credencial(self):
        repo = self.criar_repositorio("com-credencial")
        self.git(repo, "remote", "add", "origin", "https://usuario:senha-secreta@git.exemplo.com/terceiro/repo.git")
        env = self.configurar_lista()

        adicionar = self.somente_leitura(repo, env, "adicionar")
        lista = self.lista_de(env).read_text()

        self.assertEqual(0, adicionar.codigo, adicionar.saida)
        self.assertIn("url:https://git.exemplo.com/terceiro/repo.git", lista)
        self.assertNotIn("senha-secreta", lista)
        self.assertNotIn("senha-secreta", adicionar.saida)

    def test_adicionar_avisa_quando_repo_tem_hooks_path_local(self):
        repo = self.criar_repositorio("com-hookspath")
        self.git(repo, "remote", "add", "origin", "https://git.exemplo.com/terceiro/proprio.git")
        env = self.configurar_lista()

        adicionar = self.somente_leitura(repo, env, "adicionar")

        self.assertEqual(0, adicionar.codigo, adicionar.saida)
        self.assertIn("core.hooksPath", adicionar.saida)

    def test_remover_sem_terminal_e_recusado(self):
        repo = self.criar_repositorio("remover")
        env = self.configurar_lista("url:https://git.exemplo.com/terceiro/")

        remover = self.somente_leitura(repo, env, "remover", "url:https://git.exemplo.com/terceiro/")

        self.assertNotEqual(0, remover.codigo, remover.saida)
        self.assertIn("url:https://git.exemplo.com/terceiro/", self.lista_de(env).read_text())

    def test_aplicar_nao_duplica_e_apaga_regra_de_entrada_removida(self):
        repo = self.criar_repositorio("aplicar")
        env = self.configurar_lista("url:https://git.exemplo.com/a/", "url:https://git.exemplo.com/b/")

        self.assertEqual(0, self.somente_leitura(repo, env, "aplicar").codigo)
        self.assertEqual(0, self.somente_leitura(repo, env, "aplicar").codigo)
        self.assertEqual(2, len(self.regras_push_instead_of(repo, env)))

        self.lista_de(env).write_text("url:https://git.exemplo.com/a/\n")
        self.assertEqual(0, self.somente_leitura(repo, env, "aplicar").codigo)
        self.assertEqual(["https://git.exemplo.com/a/"], self.regras_push_instead_of(repo, env))

    def test_instalador_aplica_lista_existente_sem_alterar_lista(self):
        fake_home = self.temp / "home-lista"
        xdg_git = fake_home / "config/git"
        xdg_git.mkdir(parents=True)
        conteudo = "# lista particular\nurl:https://git.exemplo.com/terceiro/\n"
        (xdg_git / "somente-leitura.lista").write_text(conteudo)
        env = {"GITHOOKS_HOME": str(fake_home), "XDG_CONFIG_HOME": str(xdg_git.parent),
               "GIT_CONFIG_GLOBAL": str(self.temp / "gitconfig-lista")}

        self.assertEqual(0, self.executar(PROJECT, None, env, BASH, str(PROJECT / "install.sh")).codigo)
        segunda = self.executar(PROJECT, None, env, BASH, str(PROJECT / "install.sh"))

        self.assertEqual(0, segunda.codigo, segunda.saida)
        self.assertEqual(conteudo, (xdg_git / "somente-leitura.lista").read_text())
        self.assertTrue(os.access(xdg_git / "somente-leitura", os.X_OK))
        regras = self.executar(PROJECT, None, env, "git", "config", "--global", "--get-all",
                               "url.somente-leitura://bloqueado/.pushInsteadOf")
        self.assertEqual("https://git.exemplo.com/terceiro/", regras.saida.strip())

    # ---------------------------------------------------------------- hooks versionados

    def test_delegar_roda_hook_versionado_depois_do_global(self):
        repo = self.criar_repositorio("versionado")
        marca = self.temp / "versionado-marca.txt"
        self.criar_hook_versionado(repo, "pre-commit", f"echo versionado >> '{marca}'")
        self.criar_hook_do_repo(repo, "pre-commit", f"echo git-hooks >> '{marca}'")
        self.git(repo, "config", "--local", "hooks.versionado", ".githooks")

        commit = self.git(repo, "commit", "--allow-empty", "-m", "commit com hook versionado")

        self.assertEqual(0, commit.codigo, commit.saida)
        self.assertEqual(["versionado", "git-hooks"], marca.read_text().splitlines())

    def test_delegar_passa_a_mesma_entrada_ao_hook_versionado(self):
        repo = self.criar_repositorio("versionado-push")
        recebido = self.temp / "versionado-entrada.txt"
        recebido_repo = self.temp / "repo-entrada.txt"
        self.criar_hook_versionado(repo, "pre-push", f"cat > '{recebido}'")
        self.criar_hook_do_repo(repo, "pre-push", f"cat > '{recebido_repo}'")
        self.git(repo, "config", "--local", "hooks.versionado", ".githooks")

        push = self.pre_push(repo, self.head(repo))

        self.assertEqual(0, push.codigo, push.saida)
        self.assertIn("refs/heads/nova " + self.head(repo), recebido.read_text())
        self.assertEqual(recebido.read_text(), recebido_repo.read_text())

    def test_global_que_recusa_impede_hook_versionado(self):
        repo = self.criar_repositorio("versionado-recusa")
        marca = self.temp / "versionado-recusa.txt"
        self.criar_hook_versionado(repo, "commit-msg", f"echo rodou > '{marca}'")
        self.git(repo, "config", "--local", "hooks.versionado", ".githooks")

        commit = self.git(repo, "commit", "--allow-empty", "-m", TERMO)

        self.assertNotEqual(0, commit.codigo, commit.saida)
        self.assertFalse(marca.exists())

    def test_hook_versionado_sem_chave_nao_roda(self):
        repo = self.criar_repositorio("versionado-sem-chave")
        marca = self.temp / "sem-chave.txt"
        self.criar_hook_versionado(repo, "pre-commit", f"echo rodou > '{marca}'")

        commit = self.git(repo, "commit", "--allow-empty", "-m", "commit sem chave")

        self.assertEqual(0, commit.codigo, commit.saida)
        self.assertFalse(marca.exists())

    # ---------------------------------------------------------------- apoio

    def configurar_lista(self, *entradas):
        xdg = self.temp / f"xdg-{time.monotonic_ns()}"
        (xdg / "git").mkdir(parents=True)
        (xdg / "git/somente-leitura.lista").write_text("\n".join(entradas) + "\n")
        global_ = xdg / "gitconfig"
        global_.touch()
        return {"XDG_CONFIG_HOME": str(xdg), "GIT_CONFIG_GLOBAL": str(global_)}

    @staticmethod
    def lista_de(env):
        return Path(env["XDG_CONFIG_HOME"], "git", "somente-leitura.lista")

    def somente_leitura(self, repo, env, *args):
        return self.executar(repo, None, env, BASH, str(PROJECT / "scripts/somente-leitura"), *args)

    def regras_push_instead_of(self, repo, env):
        saida = self.git_com_env(repo, env, "config", "--global", "--get-all",
                                 "url.somente-leitura://bloqueado/.pushInsteadOf").saida.strip()
        return saida.split("\n") if saida else []

    def criar_repositorio_bare(self, nome):
        trabalho = self.criar_repositorio(nome + "-trabalho")
        self.assertEqual(0, self.git(trabalho, "commit", "--allow-empty", "-m", "segundo commit limpo").codigo)
        bare = self.temp / (nome + ".git")
        clone = self.executar(self.temp, None, {}, "git", "clone", "--bare", str(trabalho), str(bare))
        self.assertEqual(0, clone.codigo, clone.saida)
        return bare

    def criar_hook_versionado(self, repo, nome, corpo):
        self.escrever_hook(repo / ".githooks" / nome, corpo)

    def criar_hook_do_repo(self, repo, nome, corpo):
        self.escrever_hook(repo / ".git/hooks" / nome, corpo)

    @staticmethod
    def escrever_hook(arquivo, corpo):
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text("#!/bin/sh\n" + corpo + "\n")
        arquivo.chmod(0o755)

    def criar_repositorio(self, nome):
        repo = self.temp / nome
        repo.mkdir()
        self.assertEqual(0, self.git(repo, "init", "-b", "main").codigo)
        self.git(repo, "config", "user.name", "Teste Local")
        self.git(repo, "config", "user.email", "teste@example.invalid")
        self.git(repo, "config", "core.hooksPath", self.hooks_path())
        base = self.git(repo, "commit", "--allow-empty", "-m", "commit inicial limpo")
        self.assertEqual(0, base.codigo, base.saida)
        return repo

    def criar_repositorio_de_terceiro(self, nome):
        repo = self.temp / nome
        repo.mkdir()
        sem_hooks = self.temp / "sem-hooks"
        sem_hooks.mkdir(exist_ok=True)
        self.assertEqual(0, self.git(repo, "init", "-b", "main").codigo)
        self.git(repo, "config", "user.name", "Terceiro")
        self.git(repo, "config", "user.email", "terceiro@example.invalid")
        self.git(repo, "config", "core.hooksPath", str(sem_hooks).replace("\\", "/"))
        self.commit_de_terceiro(repo, "commit inicial limpo")
        self.assertEqual(0, self.git(repo, "branch", "codex-feature").codigo)
        self.commit_de_terceiro(repo, TERMO)
        return repo

    def commit_de_terceiro(self, repo, mensagem):
        commit = self.git(repo, "commit", "--allow-empty", "-m", mensagem)
        self.assertEqual(0, commit.codigo, commit.saida)

    def clonar(self, origem, nome):
        destino = self.temp / nome
        clone = self.executar(self.temp, None, {}, "git", "clone", "-c", "core.hooksPath=" + self.hooks_path(),
                              "-c", "user.name=Teste Local", "-c", "user.email=teste@example.invalid",
                              str(origem), str(destino))
        self.assertEqual(0, clone.codigo, clone.saida)
        return destino

    def pre_push(self, repo, oid):
        return self.pre_push_com(repo, f"refs/heads/nova {oid} refs/heads/nova {OID_ZERO}\n")

    def pre_push_com(self, repo, entrada, remoto="origin", url="https://example.invalid/repo"):
        return self.executar(repo, entrada, {}, BASH, str(HOOKS / "pre-push"), remoto, url)

    def head(self, repo):
        return self.rev(repo, "HEAD")

    def rev(self, repo, ref):
        return self.git(repo, "rev-parse", ref).saida.strip()

    @staticmethod
    def hooks_path():
        return str(HOOKS).replace("\\", "/")

    def git(self, repo, *args):
        return self.git_com_env(repo, {}, *args)

    def git_com_env(self, repo, env, *args):
        return self.executar(repo, None, env, "git", "-C", str(repo), *args)

    def executar(self, pasta, entrada, env, *comando):
        ambiente = {chave: valor for chave, valor in os.environ.items()
                    if chave != "EMAIL" and not chave.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
        ambiente.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.global_vazio),
                         "XDG_CONFIG_HOME": str(self.xdg_vazio)})
        ambiente.update(env)
        processo = subprocess.run(list(comando), cwd=pasta, env=ambiente, input=(entrada or "").encode(),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return Resultado(processo.returncode, processo.stdout.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
