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

    @staticmethod
    def rodar_guarda(entrada):
        processo = subprocess.run([sys.executable, "-I", str(GUARDA)], input=json.dumps(entrada).encode(),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return processo.stdout.decode().strip()


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
