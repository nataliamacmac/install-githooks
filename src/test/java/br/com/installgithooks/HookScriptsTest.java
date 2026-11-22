package br.com.installgithooks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest(classes = HookGuardTestApplication.class)
class HookScriptsTest {
    private static final Path PROJECT = Path.of(System.getProperty("user.dir")).toAbsolutePath();
    private static final Path HOOKS = PROJECT.resolve("hooks");
    private static final String BASH = findBash();
    private static final Path GLOBAL_VAZIO = criarGlobalVazio();
    private static final Path XDG_VAZIO = criarXdgVazio();
    private static final String TERMO = "feat: adicionar suporte a Claude Code";
    private static final String OID_ZERO = "0000000000000000000000000000000000000000";

    @TempDir
    Path tempDir;

    @Test
    void commitNoVerifyAindaBloqueiaMensagemProibida() throws Exception {
        Path repo = criarRepositorio("no-verify");
        String headAntes = git(repo, "rev-parse", "HEAD").output().trim();

        Result commit = git(repo, "commit", "--allow-empty", "--no-verify", "-m", "feat: adicionar suporte a Claude Code");

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertTrue(commit.output().toLowerCase().contains("claude"), commit.output());
        assertEquals(headAntes, git(repo, "rev-parse", "HEAD").output().trim());
    }

    @Test
    void recusaNomeDeBranchProibido() throws Exception {
        Path repo = criarRepositorio("branch");

        Result branch = git(repo, "branch", "codex-feature");

        assertNotEquals(0, branch.exitCode(), branch.output());
        assertFalse(Files.exists(repo.resolve(".git/refs/heads/codex-feature")));
    }

    @Test
    void prePushInspecionaCommitsECoautoria() throws Exception {
        Path repo = criarRepositorio("push");
        String base = git(repo, "rev-parse", "HEAD").output().trim();
        String tree = git(repo, "rev-parse", "HEAD^{tree}").output().trim();

        for (String mensagem : List.of(
                "feat: integrar OpenAI",
                "Co-authored-by: IA <ia@example.invalid>")) {
            Result commit = git(repo, "commit-tree", tree, "-p", base, "-m", mensagem);
            assertEquals(0, commit.exitCode(), commit.output());
            String oid = commit.output().trim();
            Result pushHook = executar(repo, "refs/heads/feature/teste " + oid + " refs/heads/feature/teste " + base + "\n",
                    Map.of(), BASH, HOOKS.resolve("pre-push").toString(), "origin", "https://example.invalid/repo");
            assertNotEquals(0, pushHook.exitCode(), pushHook.output());
        }
    }

    @Test
    void prePushValidaHistoricoQuandoOidRemotoNaoFoiBaixado() throws Exception {
        Path repo = criarRepositorio("push-remoto-novo");
        String local = git(repo, "rev-parse", "HEAD").output().trim();
        String remotoDesconhecido = "1111111111111111111111111111111111111111";

        Result pushHook = executar(repo,
                "refs/heads/main " + local + " refs/heads/main " + remotoDesconhecido + "\n",
                Map.of(), BASH, HOOKS.resolve("pre-push").toString(), "origin", "https://example.invalid/repo");

        assertEquals(0, pushHook.exitCode(), pushHook.output());
    }

    @Test
    void prePushNaoReconfereCommitQueJaEstaNoRemoto() throws Exception {
        Path repo = criarRepositorio("push-ja-no-remoto");
        String base = git(repo, "rev-parse", "HEAD").output().trim();
        String tree = git(repo, "rev-parse", "HEAD^{tree}").output().trim();
        // commit antigo com o termo, que ja foi enviado a outra branch do mesmo remoto
        String antigo = git(repo, "commit-tree", tree, "-p", base, "-m", TERMO).output().trim();
        assertEquals(0, git(repo, "update-ref", "refs/remotes/origin/desenvolvimento", antigo).exitCode());

        Result pushHook = executar(repo,
                "refs/heads/main " + antigo + " refs/heads/main " + base + "\n",
                Map.of(), BASH, HOOKS.resolve("pre-push").toString(), "origin", "https://example.invalid/repo");

        assertEquals(0, pushHook.exitCode(), pushHook.output());
    }

    @Test
    void prePushBloqueiaCommitNovoComTermoMesmoComHistoricoJaEnviado() throws Exception {
        Path repo = criarRepositorio("push-novo-com-termo");
        String base = git(repo, "rev-parse", "HEAD").output().trim();
        String tree = git(repo, "rev-parse", "HEAD^{tree}").output().trim();
        String antigo = git(repo, "commit-tree", tree, "-p", base, "-m", "feat: ajuste antigo").output().trim();
        assertEquals(0, git(repo, "update-ref", "refs/remotes/origin/desenvolvimento", antigo).exitCode());
        String novo = git(repo, "commit-tree", tree, "-p", antigo, "-m", TERMO).output().trim();

        Result pushHook = executar(repo,
                "refs/heads/main " + novo + " refs/heads/main " + base + "\n",
                Map.of(), BASH, HOOKS.resolve("pre-push").toString(), "origin", "https://example.invalid/repo");

        assertNotEquals(0, pushHook.exitCode(), pushHook.output());
    }

    @Test
    void prePushBloqueiaTermoJaEnviadoSoParaOutroRemoto() throws Exception {
        Path repo = criarRepositorio("push-outro-remoto");
        String base = git(repo, "rev-parse", "HEAD").output().trim();
        String tree = git(repo, "rev-parse", "HEAD^{tree}").output().trim();
        String antigo = git(repo, "commit-tree", tree, "-p", base, "-m", TERMO).output().trim();
        // o commit esta so no remoto "origin"; o push agora vai para o remoto "interno"
        assertEquals(0, git(repo, "update-ref", "refs/remotes/origin/desenvolvimento", antigo).exitCode());

        Result pushHook = executar(repo,
                "refs/heads/main " + antigo + " refs/heads/main " + base + "\n",
                Map.of(), BASH, HOOKS.resolve("pre-push").toString(), "interno", "https://example.invalid/outro");

        assertNotEquals(0, pushHook.exitCode(), pushHook.output());
    }

    @Test
    void amendProprioComTermoEBloqueadoMesmoComNoVerify() throws Exception {
        Path repo = criarRepositorio("amend");
        String headAntes = head(repo);

        Result amend = git(repo, "commit", "--amend", "--allow-empty", "--no-verify", "-m", TERMO);

        assertNotEquals(0, amend.exitCode(), amend.output());
        assertEquals(headAntes, head(repo));
    }

    @Test
    void trocarEmailDoCommitterPorVariavelNaoDesviaBloqueio() throws Exception {
        Path repo = criarRepositorio("committer-env");
        String headAntes = head(repo);

        Result commit = gitComEnv(repo, Map.of("GIT_COMMITTER_EMAIL", "outro@example.invalid"),
                "commit", "--allow-empty", "--no-verify", "-m", TERMO);

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertEquals(headAntes, head(repo));
    }

    @Test
    void commitProprioSemTermoEPermitido() throws Exception {
        Path repo = criarRepositorio("limpo");

        Result commit = git(repo, "commit", "--allow-empty", "--no-verify", "-m", "feat: ajustar validacao");

        assertEquals(0, commit.exitCode(), commit.output());
    }

    @Test
    void cloneFetchEPullDeCommitDeTerceiroComTermoSaoPermitidos() throws Exception {
        Path origem = criarRepositorioDeTerceiro("origem-clone");

        Path clone = clonar(origem, "clone");
        assertEquals(git(origem, "rev-parse", "main").output().trim(), head(clone));

        commitDeTerceiro(origem, "feat: integrar OpenAI");
        Result fetch = git(clone, "fetch", "origin");
        assertEquals(0, fetch.exitCode(), fetch.output());
        Result pull = git(clone, "pull", "--ff-only");
        assertEquals(0, pull.exitCode(), pull.output());
        assertEquals(git(origem, "rev-parse", "main").output().trim(), head(clone));
    }

    @Test
    void branchLocalAPartirDeCommitDeTerceiroEPermitida() throws Exception {
        Path clone = clonar(criarRepositorioDeTerceiro("origem-branch"), "clone-branch");

        Result checkout = git(clone, "checkout", "-b", "x", "origin/main");

        assertEquals(0, checkout.exitCode(), checkout.output());
    }

    @Test
    void cherryPickDeCommitDeTerceiroComTermoEBloqueado() throws Exception {
        Path clone = clonar(criarRepositorioDeTerceiro("origem-pick"), "clone-pick");
        assertEquals(0, git(clone, "checkout", "-b", "base", "origin/main~1").exitCode());
        assertEquals(0, git(clone, "commit", "--allow-empty", "-m", "feat: base propria").exitCode());
        String headAntes = head(clone);

        Result pick = git(clone, "cherry-pick", "--allow-empty", "origin/main");

        assertNotEquals(0, pick.exitCode(), pick.output());
        assertEquals(headAntes, git(clone, "rev-parse", "refs/heads/base").output().trim());
    }

    @Test
    void rebaseEAmendDeCommitDeTerceiroComTermoSaoBloqueados() throws Exception {
        Path clone = clonar(criarRepositorioDeTerceiro("origem-rebase"), "clone-rebase");
        assertEquals(0, git(clone, "checkout", "-b", "base", "origin/main~1").exitCode());
        assertEquals(0, git(clone, "commit", "--allow-empty", "-m", "feat: base propria").exitCode());
        assertEquals(0, git(clone, "checkout", "-b", "x", "origin/main").exitCode());
        String terceiro = head(clone);

        Result rebase = git(clone, "rebase", "--keep-empty", "--onto", "base", "origin/main~1", "x");
        assertNotEquals(0, rebase.exitCode(), rebase.output());
        assertEquals(terceiro, git(clone, "rev-parse", "refs/heads/x").output().trim());
        git(clone, "rebase", "--abort");

        Result amend = git(clone, "commit", "--amend", "--allow-empty", "--no-edit", "--no-verify");
        assertNotEquals(0, amend.exitCode(), amend.output());
        assertEquals(terceiro, git(clone, "rev-parse", "refs/heads/x").output().trim());
    }

    @Test
    void semIdentidadeDeterminavelValidaTudo() throws Exception {
        Path origem = criarRepositorioDeTerceiro("origem-sem-identidade");
        Path repo = Files.createDirectory(tempDir.resolve("sem-identidade"));
        assertEquals(0, git(repo, "init", "-b", "main").exitCode());
        git(repo, "config", "core.hooksPath", hooksPath());
        git(repo, "config", "user.useConfigOnly", "true");
        git(repo, "remote", "add", "origin", origem.toString());

        Result fetch = git(repo, "fetch", "origin");
        assertEquals(0, fetch.exitCode(), fetch.output());
        Result branch = git(repo, "branch", "y", "origin/main");
        assertNotEquals(0, branch.exitCode(), branch.output());
    }

    @Test
    void semUserEmailUsaIdentidadeEfetivaDoGit() throws Exception {
        Path origem = criarRepositorioDeTerceiro("origem-identidade-efetiva");
        Path repo = Files.createDirectory(tempDir.resolve("identidade-efetiva"));
        Map<String, String> env = Map.of("EMAIL", "teste@example.invalid");
        assertEquals(0, git(repo, "init", "-b", "main").exitCode());
        git(repo, "config", "core.hooksPath", hooksPath());
        git(repo, "config", "user.name", "Teste Local");
        git(repo, "remote", "add", "origin", origem.toString());

        assertEquals(0, gitComEnv(repo, env, "fetch", "origin").exitCode());
        Result branch = gitComEnv(repo, env, "checkout", "-b", "y", "origin/main");
        assertEquals(0, branch.exitCode(), branch.output());
        Result commit = gitComEnv(repo, env, "commit", "--allow-empty", "--no-verify", "-m", TERMO);
        assertNotEquals(0, commit.exitCode(), commit.output());
    }

    @Test
    void nomeProibidoSoEmRefsRemotesEPermitido() throws Exception {
        Path clone = clonar(criarRepositorioDeTerceiro("origem-nome"), "clone-nome");

        assertEquals(0, git(clone, "rev-parse", "--verify", "refs/remotes/origin/codex-feature").exitCode());
        Result branch = git(clone, "branch", "codex-feature", "origin/codex-feature");
        assertNotEquals(0, branch.exitCode(), branch.output());
    }

    @Test
    void prePushIgnoraHistoricoDeTerceiroEmBranchNova() throws Exception {
        Path clone = clonar(criarRepositorioDeTerceiro("origem-push"), "clone-push");
        assertEquals(0, git(clone, "checkout", "-b", "nova", "origin/main").exitCode());
        assertEquals(0, git(clone, "commit", "--allow-empty", "-m", "feat: ajuste proprio").exitCode());
        String limpo = head(clone);

        Result permitido = prePush(clone, limpo);
        assertEquals(0, permitido.exitCode(), permitido.output());

        String tree = git(clone, "rev-parse", "HEAD^{tree}").output().trim();
        String proibido = git(clone, "commit-tree", tree, "-p", limpo, "-m", TERMO).output().trim();
        Result bloqueado = prePush(clone, proibido);
        assertNotEquals(0, bloqueado.exitCode(), bloqueado.output());
    }

    @Test
    void scannerAvisaSobreCoreHooksPathLocal() throws Exception {
        Path root = Files.createDirectory(tempDir.resolve("repos"));
        Path custom = Files.createDirectory(root.resolve("repo-customizado"));
        git(custom, "init");
        git(custom, "config", "--local", "core.hooksPath", "./hooks-especiais");

        Result scan = executar(root, null, Map.of(), BASH,
                PROJECT.resolve("scripts/scan-custom-hooks.sh").toString(), root.toString());

        assertEquals(0, scan.exitCode(), scan.output());
        assertTrue(scan.output().contains("repo-customizado"), scan.output());
        assertTrue(scan.output().contains("substitui os hooks globais"), scan.output());
    }

    @Test
    void mensagemComNomesDeModeloESubagenteEBloqueada() throws Exception {
        Path repo = criarRepositorio("modelos");
        String headAntes = head(repo);

        for (String mensagem : List.of("feat: validado por Opus", "fix: executado pelo Sonnet", "docs: revisao Haiku",
                "chore: tarefa do subagente", "chore: sub-agent concluiu", "chore: subagentes em paralelo")) {
            Result commit = git(repo, "commit", "--allow-empty", "-m", mensagem);
            assertNotEquals(0, commit.exitCode(), mensagem + " -> " + commit.output());
        }
        assertEquals(headAntes, head(repo));
    }

    @Test
    void preCommitBloqueiaNomeDeArquivoComTermoDeIa() throws Exception {
        Path repo = criarRepositorio("pc-nome");
        Files.writeString(repo.resolve("relatorio-opus.md"), "conteudo neutro\n");
        assertEquals(0, git(repo, "add", "relatorio-opus.md").exitCode());
        String headAntes = head(repo);

        Result commit = git(repo, "commit", "-m", "docs: relatorio");

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertTrue(commit.output().contains("nome de arquivo"), commit.output());
        assertEquals(headAntes, head(repo));
    }

    @Test
    void preCommitBloqueiaConteudoComTermoDeIa() throws Exception {
        Path repo = criarRepositorio("pc-conteudo");
        for (String linha : List.of("Validado pelo Opus.", "executor: Sonnet", "revisao do Haiku", "feito por subagentes",
                "Gerado com Claude Code", "usa o OpenAI", "modelo gpt-4o", "via Copilot")) {
            Files.writeString(repo.resolve("nota.md"), linha + "\n");
            assertEquals(0, git(repo, "add", "nota.md").exitCode());

            Result commit = git(repo, "commit", "-m", "docs: nota");

            assertNotEquals(0, commit.exitCode(), linha + " -> " + commit.output());
            assertTrue(commit.output().contains("conteudo adicionado em nota.md"), commit.output());
            assertEquals(0, git(repo, "reset", "-q").exitCode());
        }
    }

    @Test
    void preCommitPermiteConteudoELinhasRemovidasSemTermo() throws Exception {
        Path repo = criarRepositorio("pc-limpo");
        Files.writeString(repo.resolve("Consulta.java"),
                "// cursor do banco, codex de leis e gemini do zodiaco; GPT e a tabela de particoes\n");
        assertEquals(0, git(repo, "add", "Consulta.java").exitCode());

        Result commit = git(repo, "commit", "-m", "feat: consulta");

        assertEquals(0, commit.exitCode(), commit.output());
    }

    @Test
    void preCommitPermiteUsoTecnicoDeTermoAmbiguoEmCodigo() throws Exception {
        Path repo = criarRepositorio("pc-codigo");
        Map<String, String> arquivos = Map.of(
                "estilo.scss", ".botao {\n  cursor: pointer;\n}\n",
                "Lista.vue", "<template><div style=\"cursor: pointer\">Haiku</div></template>\n",
                "consulta.sql", "DECLARE c CURSOR FOR SELECT opus, sonnet FROM obras;\n",
                "pagina.html", "<p>Sonnet 18 e o codex de leis</p>\n",
                "cursor.ts", "export const cursor = 1;\n");
        for (Map.Entry<String, String> arquivo : arquivos.entrySet()) {
            Files.writeString(repo.resolve(arquivo.getKey()), arquivo.getValue());
            assertEquals(0, git(repo, "add", arquivo.getKey()).exitCode());
        }

        Result commit = git(repo, "commit", "-m", "feat: tela");

        assertEquals(0, commit.exitCode(), commit.output());
    }

    @Test
    void preCommitBloqueiaTermoAmbiguoEmTextoDeProsa() throws Exception {
        Path repo = criarRepositorio("pc-prosa");
        for (String[] caso : List.of(
                new String[] {"nota.md", "Feito no editor Cursor.\n"},
                new String[] {"leia.txt", "revisado com o Gemini\n"},
                new String[] {"guia.adoc", "rodado no Codex\n"},
                new String[] {"notas-cursor.md", "conteudo neutro\n"})) {
            Files.writeString(repo.resolve(caso[0]), caso[1]);
            assertEquals(0, git(repo, "add", caso[0]).exitCode());

            Result commit = git(repo, "commit", "-m", "docs: nota");

            assertNotEquals(0, commit.exitCode(), caso[0] + " -> " + commit.output());
            assertEquals(0, git(repo, "reset", "-q").exitCode());
            Files.delete(repo.resolve(caso[0]));
        }
    }

    @Test
    void preCommitBloqueiaTermoForteMesmoEmCodigo() throws Exception {
        Path repo = criarRepositorio("pc-forte");
        for (String[] caso : List.of(
                new String[] {"estilo.scss", "/* gerado pelo Copilot */\n"},
                new String[] {"consulta.sql", "-- escrito com Claude Code\n"},
                new String[] {"Lista.vue", "<!-- feito por subagentes -->\n"})) {
            Files.writeString(repo.resolve(caso[0]), caso[1]);
            assertEquals(0, git(repo, "add", caso[0]).exitCode());

            Result commit = git(repo, "commit", "-m", "feat: tela");

            assertNotEquals(0, commit.exitCode(), caso[0] + " -> " + commit.output());
            assertTrue(commit.output().contains("conteudo adicionado em " + caso[0]), commit.output());
            assertEquals(0, git(repo, "reset", "-q").exitCode());
            Files.delete(repo.resolve(caso[0]));
        }
    }

    @Test
    void preCommitSoVeLinhasAdicionadasNaoAsRemovidas() throws Exception {
        Path repo = criarRepositorio("pc-remocao");
        Files.writeString(repo.resolve("nota.md"), "linha antiga\n");
        assertEquals(0, git(repo, "add", "nota.md").exitCode());
        assertEquals(0, git(repo, "commit", "-m", "docs: nota").exitCode());
        // simula um termo ja existente no historico e sendo removido agora
        Files.writeString(repo.resolve("nota.md"), "linha nova limpa\n");
        assertEquals(0, git(repo, "add", "nota.md").exitCode());

        Result commit = git(repo, "commit", "-m", "docs: limpar nota");

        assertEquals(0, commit.exitCode(), commit.output());
    }

    @Test
    void preCommitPermiteMoverArquivoQueJaTinhaOTermoNoHistorico() throws Exception {
        Path repo = criarRepositorio("pc-mover");
        Files.writeString(repo.resolve("config.json"), "{\n  \"nome\": \"copilot-teste\",\n  \"x\": 1\n}\n");
        assertEquals(0, git(repo, "add", "config.json").exitCode());
        // o termo ja esta no historico: entrou antes do hook existir
        assertEquals(0, git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-m", "docs: config").exitCode());
        Files.createDirectories(repo.resolve("novo"));
        assertEquals(0, git(repo, "mv", "config.json", "novo/config.json").exitCode());

        Result commit = git(repo, "commit", "-m", "chore: move a config");

        assertEquals(0, commit.exitCode(), commit.output());
    }

    @Test
    void preCommitBloqueiaTermoNovoEmArquivoMovido() throws Exception {
        Path repo = criarRepositorio("pc-mover-novo");
        String base = "{\n  \"nome\": \"copilot-teste\",\n  \"a\": 1,\n  \"b\": 2,\n  \"c\": 3,\n  \"d\": 4,\n"
                + "  \"e\": 5,\n  \"f\": 6,\n  \"g\": 7,\n  \"h\": 8\n";
        Files.writeString(repo.resolve("config.json"), base + "}\n");
        assertEquals(0, git(repo, "add", "config.json").exitCode());
        assertEquals(0, git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-m", "docs: config").exitCode());
        Files.createDirectories(repo.resolve("novo"));
        assertEquals(0, git(repo, "mv", "config.json", "novo/config.json").exitCode());
        Files.writeString(repo.resolve("novo/config.json"), base.replace("\"h\": 8", "\"h\": \"Claude Code\"") + "}\n");
        assertEquals(0, git(repo, "add", "novo/config.json").exitCode());

        Result commit = git(repo, "commit", "-m", "chore: move e edita a config");

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertTrue(commit.output().contains("Claude Code"), commit.output());
        assertFalse(commit.output().contains("copilot-teste"), commit.output());
    }

    @Test
    void preCommitRespeitaCaminhosIsentosDoRepositorio() throws Exception {
        Path repo = criarRepositorio("pc-isento");
        assertEquals(0, git(repo, "config", "--local", "--add", "hooks.termosIaIgnorar", "hooks/_bloquear_*").exitCode());
        Files.createDirectories(repo.resolve("hooks"));
        Files.writeString(repo.resolve("hooks/_bloquear_exemplo"), "grep -E 'opus|sonnet'\n");
        Files.writeString(repo.resolve("outro.txt"), "Sonnet\n");
        assertEquals(0, git(repo, "add", "hooks/_bloquear_exemplo").exitCode());

        Result isento = git(repo, "commit", "-m", "chore: hook");
        assertEquals(0, isento.exitCode(), isento.output());

        assertEquals(0, git(repo, "add", "outro.txt").exitCode());
        Result bloqueado = git(repo, "commit", "-m", "chore: outro");
        assertNotEquals(0, bloqueado.exitCode(), bloqueado.output());
    }

    @Test
    void instaladorEIdempotenteEConfiguraHooksGlobaisIsolados() throws Exception {
        Path fakeHome = Files.createDirectory(tempDir.resolve("home"));
        Path xdgHome = Files.createDirectory(fakeHome.resolve("config"));
        Path globalConfig = tempDir.resolve("gitconfig");
        Map<String, String> env = Map.of(
                "GITHOOKS_HOME", fakeHome.toString(),
                "XDG_CONFIG_HOME", xdgHome.toString(),
                "GIT_CONFIG_GLOBAL", globalConfig.toString());

        Result first = executar(PROJECT, null, env, BASH, PROJECT.resolve("install.sh").toString());
        assertEquals(0, first.exitCode(), first.output());
        Result second = executar(PROJECT, null, env, BASH, PROJECT.resolve("install.sh").toString());
        assertEquals(0, second.exitCode(), second.output());

        String hooksPath = executar(PROJECT, null, env, "git", "config", "--global", "--get", "core.hooksPath").output().trim();
        assertTrue(Files.isRegularFile(Path.of(hooksPath, "reference-transaction")));
        assertTrue(Files.isRegularFile(Path.of(hooksPath).getParent().resolve("scan-custom-hooks.sh")));
        try (Stream<Path> files = Files.list(Path.of(hooksPath))) {
            assertFalse(files.anyMatch(path -> path.getFileName().toString().contains("backup")));
        }
    }

    @Test
    void semListaSomenteLeituraPushContinuaPermitido() throws Exception {
        Path origem = criarRepositorioBare("origem-livre");
        Path repo = clonar(origem, "livre");
        Map<String, String> env = configurarLista();
        assertEquals(0, gitComEnv(repo, env, "commit", "--allow-empty", "-m", "commit livre").exitCode());

        Result push = gitComEnv(repo, env, "push", "origin", "main");

        assertEquals(0, push.exitCode(), push.output());
    }

    @Test
    void pushParaUrlDaListaERecusadoPeloHook() throws Exception {
        Path origem = criarRepositorioBare("origem-trava-url");
        Path repo = clonar(origem, "trava-url");
        Map<String, String> env = configurarLista("url:" + origem);

        Result push = gitComEnv(repo, env, "push", "origin", "HEAD:refs/heads/nova");

        assertNotEquals(0, push.exitCode(), push.output());
        assertTrue(push.output().contains("só leitura"), push.output());
        assertNotEquals(0, git(origem, "rev-parse", "--verify", "refs/heads/nova").exitCode());
    }

    @Test
    void pushInsteadOfBloqueiaEnvioMesmoSemHooksEPreservaFetch() throws Exception {
        Path origem = criarRepositorioBare("origem-insteadof");
        Path repo = clonar(origem, "insteadof");
        Map<String, String> env = configurarLista("url:" + origem);
        Result aplicar = somenteLeitura(repo, env, "aplicar");
        assertEquals(0, aplicar.exitCode(), aplicar.output());

        String pushUrl = gitComEnv(repo, env, "remote", "get-url", "--push", "origin").output().trim();
        Result push = gitComEnv(repo, env, "-c", "core.hooksPath=/dev/null", "push", "--no-verify", "origin", "HEAD:refs/heads/nova");
        Result fetch = gitComEnv(repo, env, "fetch", "origin");

        assertTrue(pushUrl.startsWith("somente-leitura://bloqueado/"), pushUrl);
        assertNotEquals(0, push.exitCode(), push.output());
        assertEquals(0, fetch.exitCode(), fetch.output());
    }

    @Test
    void repoEmPastaDaListaNaoFazCommitNemPush() throws Exception {
        Path origem = criarRepositorioBare("origem-pasta");
        Path repo = clonar(origem, "pasta-travada");
        Map<String, String> env = configurarLista("pasta:" + repo.toRealPath());
        String headAntes = head(repo);

        Result commit = gitComEnv(repo, env, "commit", "--allow-empty", "--no-verify", "-m", "commit local");
        Result push = gitComEnv(repo, env, "push", "origin", "HEAD:refs/heads/nova");

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertTrue(commit.output().contains("só leitura"), commit.output());
        assertEquals(headAntes, head(repo));
        assertNotEquals(0, push.exitCode(), push.output());
        assertNotEquals(0, git(origem, "rev-parse", "--verify", "refs/heads/nova").exitCode());
    }

    @Test
    void repoTravadoRecusaStashResetEApagarBranch() throws Exception {
        Path origem = criarRepositorioBare("origem-destrutivo");
        Path repo = clonar(origem, "destrutivo");
        Map<String, String> env = configurarLista("url:" + origem);
        String headAntes = head(repo);
        Files.writeString(repo.resolve("rascunho.txt"), "rascunho\n");
        assertEquals(0, gitComEnv(repo, env, "add", "rascunho.txt").exitCode());

        Result stash = gitComEnv(repo, env, "stash");
        Result reset = gitComEnv(repo, env, "reset", "--soft", "HEAD~1");
        Result apagar = gitComEnv(repo, env, "update-ref", "-d", "refs/heads/main");

        assertNotEquals(0, stash.exitCode(), stash.output());
        assertNotEquals(0, reset.exitCode(), reset.output());
        assertNotEquals(0, apagar.exitCode(), apagar.output());
        assertEquals(headAntes, head(repo));
    }

    @Test
    void repoTravadoRecebeFetchPullTagEClone() throws Exception {
        Path origemTrabalho = criarRepositorio("origem-trabalho");
        Path origem = tempDir.resolve("origem-recebe.git");
        assertEquals(0, executar(tempDir, null, Map.of(), "git", "clone", "--bare",
                origemTrabalho.toString(), origem.toString()).exitCode());
        Map<String, String> env = configurarLista("url:" + origem);
        Path repo = tempDir.resolve("recebe");
        Result clone = executar(tempDir, null, env, "git", "clone",
                "-c", "core.hooksPath=" + hooksPath(), origem.toString(), repo.toString());
        assertEquals(0, clone.exitCode(), clone.output());

        assertEquals(0, git(origemTrabalho, "commit", "--allow-empty", "-m", "commit novo no remoto").exitCode());
        assertEquals(0, git(origemTrabalho, "tag", "v1").exitCode());
        assertEquals(0, git(origemTrabalho, "push", origem.toString(), "main", "v1").exitCode());

        Result pull = gitComEnv(repo, env, "pull", "--ff-only", "origin", "main");
        Result tags = gitComEnv(repo, env, "fetch", "--tags", "origin");
        Result branch = gitComEnv(repo, env, "switch", "-c", "leitura", "origin/main");

        assertEquals(0, pull.exitCode(), pull.output());
        assertEquals(0, tags.exitCode(), tags.output());
        assertEquals(0, branch.exitCode(), branch.output());
        assertEquals(head(origemTrabalho), head(repo));
    }

    @Test
    void worktreeDeRepoTravadoNaoFazCommitNemPush() throws Exception {
        Path origem = criarRepositorioBare("origem-worktree");
        Path repo = clonar(origem, "principal-travado");
        Map<String, String> env = configurarLista("pasta:" + repo.toRealPath());
        Path worktree = tempDir.resolve("wt-travado");
        Result add = gitComEnv(repo, env, "worktree", "add", "--detach", worktree.toString());
        assertEquals(0, add.exitCode(), add.output());
        String headAntes = head(worktree);

        Result commit = gitComEnv(worktree, env, "commit", "--allow-empty", "--no-verify", "-m", "commit no worktree");
        Result push = gitComEnv(worktree, env, "push", "origin", "HEAD:refs/heads/nova");

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertEquals(headAntes, head(worktree));
        assertNotEquals(0, push.exitCode(), push.output());
    }

    @Test
    void adicionarSemArgumentoGravaUrlDoOriginSemCredencial() throws Exception {
        Path repo = criarRepositorio("com-credencial");
        git(repo, "remote", "add", "origin", "https://usuario:senha-secreta@git.exemplo.com/terceiro/repo.git");
        Map<String, String> env = configurarLista();

        Result adicionar = somenteLeitura(repo, env, "adicionar");
        String lista = Files.readString(listaDe(env));

        assertEquals(0, adicionar.exitCode(), adicionar.output());
        assertTrue(lista.contains("url:https://git.exemplo.com/terceiro/repo.git"), lista);
        assertFalse(lista.contains("senha-secreta"), lista);
        assertFalse(adicionar.output().contains("senha-secreta"), adicionar.output());
    }

    @Test
    void adicionarAvisaQuandoRepoTemHooksPathLocal() throws Exception {
        Path repo = criarRepositorio("com-hookspath");
        git(repo, "remote", "add", "origin", "https://git.exemplo.com/terceiro/proprio.git");
        Map<String, String> env = configurarLista();

        Result adicionar = somenteLeitura(repo, env, "adicionar");

        assertEquals(0, adicionar.exitCode(), adicionar.output());
        assertTrue(adicionar.output().contains("core.hooksPath"), adicionar.output());
    }

    @Test
    void removerSemTerminalERecusado() throws Exception {
        Path repo = criarRepositorio("remover");
        Map<String, String> env = configurarLista("url:https://git.exemplo.com/terceiro/");

        Result remover = somenteLeitura(repo, env, "remover", "url:https://git.exemplo.com/terceiro/");

        assertNotEquals(0, remover.exitCode(), remover.output());
        assertTrue(Files.readString(listaDe(env)).contains("url:https://git.exemplo.com/terceiro/"));
    }

    @Test
    void aplicarNaoDuplicaEApagaRegraDeEntradaRemovida() throws Exception {
        Path repo = criarRepositorio("aplicar");
        Map<String, String> env = configurarLista("url:https://git.exemplo.com/a/", "url:https://git.exemplo.com/b/");

        assertEquals(0, somenteLeitura(repo, env, "aplicar").exitCode());
        assertEquals(0, somenteLeitura(repo, env, "aplicar").exitCode());
        assertEquals(2, regrasPushInsteadOf(repo, env).size());

        Files.writeString(listaDe(env), "url:https://git.exemplo.com/a/\n");
        assertEquals(0, somenteLeitura(repo, env, "aplicar").exitCode());
        assertEquals(List.of("https://git.exemplo.com/a/"), regrasPushInsteadOf(repo, env));
    }

    @Test
    void instaladorAplicaListaExistenteSemAlterarLista() throws Exception {
        Path fakeHome = Files.createDirectory(tempDir.resolve("home-lista"));
        Path xdgHome = Files.createDirectories(fakeHome.resolve("config/git"));
        Path globalConfig = tempDir.resolve("gitconfig-lista");
        String conteudo = "# lista particular\nurl:https://git.exemplo.com/terceiro/\n";
        Files.writeString(xdgHome.resolve("somente-leitura.lista"), conteudo);
        Map<String, String> env = Map.of(
                "GITHOOKS_HOME", fakeHome.toString(),
                "XDG_CONFIG_HOME", xdgHome.getParent().toString(),
                "GIT_CONFIG_GLOBAL", globalConfig.toString());

        assertEquals(0, executar(PROJECT, null, env, BASH, PROJECT.resolve("install.sh").toString()).exitCode());
        Result segunda = executar(PROJECT, null, env, BASH, PROJECT.resolve("install.sh").toString());

        assertEquals(0, segunda.exitCode(), segunda.output());
        assertEquals(conteudo, Files.readString(xdgHome.resolve("somente-leitura.lista")));
        assertTrue(Files.isExecutable(xdgHome.resolve("somente-leitura")));
        Result regras = executar(PROJECT, null, env, "git", "config", "--global", "--get-all",
                "url.somente-leitura://bloqueado/.pushInsteadOf");
        assertEquals("https://git.exemplo.com/terceiro/", regras.output().trim());
    }

    @Test
    void delegarRodaHookVersionadoDepoisDoGlobal() throws Exception {
        Path repo = criarRepositorio("versionado");
        Path marca = tempDir.resolve("versionado-marca.txt");
        criarHookVersionado(repo, "pre-commit", "echo versionado >> '" + marca + "'");
        criarHookDoRepo(repo, "pre-commit", "echo git-hooks >> '" + marca + "'");
        git(repo, "config", "--local", "hooks.versionado", ".githooks");

        Result commit = git(repo, "commit", "--allow-empty", "-m", "commit com hook versionado");

        assertEquals(0, commit.exitCode(), commit.output());
        assertEquals(List.of("versionado", "git-hooks"), Files.readAllLines(marca));
    }

    @Test
    void delegarPassaAMesmaEntradaAoHookVersionado() throws Exception {
        Path repo = criarRepositorio("versionado-push");
        Path recebido = tempDir.resolve("versionado-entrada.txt");
        Path recebidoRepo = tempDir.resolve("repo-entrada.txt");
        criarHookVersionado(repo, "pre-push", "cat > '" + recebido + "'");
        criarHookDoRepo(repo, "pre-push", "cat > '" + recebidoRepo + "'");
        git(repo, "config", "--local", "hooks.versionado", ".githooks");

        Result push = prePush(repo, head(repo));

        assertEquals(0, push.exitCode(), push.output());
        assertTrue(Files.readString(recebido).contains("refs/heads/nova " + head(repo)));
        assertEquals(Files.readString(recebido), Files.readString(recebidoRepo));
    }

    @Test
    void globalQueRecusaImpedeHookVersionado() throws Exception {
        Path repo = criarRepositorio("versionado-recusa");
        Path marca = tempDir.resolve("versionado-recusa.txt");
        criarHookVersionado(repo, "commit-msg", "echo rodou > '" + marca + "'");
        git(repo, "config", "--local", "hooks.versionado", ".githooks");

        Result commit = git(repo, "commit", "--allow-empty", "-m", TERMO);

        assertNotEquals(0, commit.exitCode(), commit.output());
        assertFalse(Files.exists(marca));
    }

    @Test
    void hookVersionadoSemChaveNaoRoda() throws Exception {
        Path repo = criarRepositorio("versionado-sem-chave");
        Path marca = tempDir.resolve("sem-chave.txt");
        criarHookVersionado(repo, "pre-commit", "echo rodou > '" + marca + "'");

        Result commit = git(repo, "commit", "--allow-empty", "-m", "commit sem chave");

        assertEquals(0, commit.exitCode(), commit.output());
        assertFalse(Files.exists(marca));
    }

    private Map<String, String> configurarLista(String... entradas) throws Exception {
        Path xdg = Files.createDirectories(tempDir.resolve("xdg-" + System.nanoTime()));
        Path git = Files.createDirectories(xdg.resolve("git"));
        Files.writeString(git.resolve("somente-leitura.lista"), String.join("\n", entradas) + "\n");
        Path global = Files.createFile(xdg.resolve("gitconfig"));
        return Map.of("XDG_CONFIG_HOME", xdg.toString(), "GIT_CONFIG_GLOBAL", global.toString());
    }

    private static Path listaDe(Map<String, String> env) {
        return Path.of(env.get("XDG_CONFIG_HOME"), "git", "somente-leitura.lista");
    }

    private static Result somenteLeitura(Path repo, Map<String, String> env, String... args) throws Exception {
        List<String> command = new ArrayList<>();
        command.add(BASH);
        command.add(PROJECT.resolve("scripts/somente-leitura").toString());
        command.addAll(List.of(args));
        return executar(repo, null, env, command.toArray(String[]::new));
    }

    private static List<String> regrasPushInsteadOf(Path repo, Map<String, String> env) throws Exception {
        String saida = gitComEnv(repo, env, "config", "--global", "--get-all",
                "url.somente-leitura://bloqueado/.pushInsteadOf").output().trim();
        return saida.isEmpty() ? List.of() : List.of(saida.split("\n"));
    }

    private Path criarRepositorioBare(String nome) throws Exception {
        Path trabalho = criarRepositorio(nome + "-trabalho");
        assertEquals(0, git(trabalho, "commit", "--allow-empty", "-m", "segundo commit limpo").exitCode());
        Path bare = tempDir.resolve(nome + ".git");
        Result clone = executar(tempDir, null, Map.of(), "git", "clone", "--bare", trabalho.toString(), bare.toString());
        assertEquals(0, clone.exitCode(), clone.output());
        return bare;
    }

    private static void criarHookVersionado(Path repo, String nome, String corpo) throws Exception {
        Path pasta = Files.createDirectories(repo.resolve(".githooks"));
        escreverHook(pasta.resolve(nome), corpo);
    }

    private static void criarHookDoRepo(Path repo, String nome, String corpo) throws Exception {
        escreverHook(repo.resolve(".git/hooks").resolve(nome), corpo);
    }

    private static void escreverHook(Path arquivo, String corpo) throws Exception {
        Files.createDirectories(arquivo.getParent());
        Files.writeString(arquivo, "#!/bin/sh\n" + corpo + "\n");
        assertTrue(arquivo.toFile().setExecutable(true));
    }

    private Path criarRepositorio(String nome) throws Exception {
        Path repo = Files.createDirectory(tempDir.resolve(nome));
        assertEquals(0, git(repo, "init", "-b", "main").exitCode());
        git(repo, "config", "user.name", "Teste Local");
        git(repo, "config", "user.email", "teste@example.invalid");
        git(repo, "config", "core.hooksPath", hooksPath());
        Result base = git(repo, "commit", "--allow-empty", "-m", "commit inicial limpo");
        assertEquals(0, base.exitCode(), base.output());
        return repo;
    }

    private Path criarRepositorioDeTerceiro(String nome) throws Exception {
        Path repo = Files.createDirectory(tempDir.resolve(nome));
        Path semHooks = Files.createDirectories(tempDir.resolve("sem-hooks"));
        assertEquals(0, git(repo, "init", "-b", "main").exitCode());
        git(repo, "config", "user.name", "Terceiro");
        git(repo, "config", "user.email", "terceiro@example.invalid");
        git(repo, "config", "core.hooksPath", semHooks.toString().replace('\\', '/'));
        commitDeTerceiro(repo, "commit inicial limpo");
        assertEquals(0, git(repo, "branch", "codex-feature").exitCode());
        commitDeTerceiro(repo, TERMO);
        return repo;
    }

    private static void commitDeTerceiro(Path repo, String mensagem) throws Exception {
        Result commit = git(repo, "commit", "--allow-empty", "-m", mensagem);
        assertEquals(0, commit.exitCode(), commit.output());
    }

    private Path clonar(Path origem, String nome) throws Exception {
        Path destino = tempDir.resolve(nome);
        Result clone = executar(tempDir, null, Map.of(), "git", "clone",
                "-c", "core.hooksPath=" + hooksPath(),
                "-c", "user.name=Teste Local",
                "-c", "user.email=teste@example.invalid",
                origem.toString(), destino.toString());
        assertEquals(0, clone.exitCode(), clone.output());
        return destino;
    }

    private static Result prePush(Path repo, String oid) throws Exception {
        return executar(repo, "refs/heads/nova " + oid + " refs/heads/nova " + OID_ZERO + "\n",
                Map.of(), BASH, HOOKS.resolve("pre-push").toString(), "origin", "https://example.invalid/repo");
    }

    private static String head(Path repo) throws Exception {
        return git(repo, "rev-parse", "HEAD").output().trim();
    }

    private static String hooksPath() {
        return HOOKS.toString().replace('\\', '/');
    }

    private static Result git(Path repo, String... args) throws Exception {
        return gitComEnv(repo, Map.of(), args);
    }

    private static Result gitComEnv(Path repo, Map<String, String> env, String... args) throws Exception {
        List<String> command = new ArrayList<>();
        command.add("git");
        command.add("-C");
        command.add(repo.toString());
        command.addAll(List.of(args));
        return executar(repo, null, env, command.toArray(String[]::new));
    }

    private static Result executar(Path directory, String stdin, Map<String, String> env, String... command) throws Exception {
        ProcessBuilder builder = new ProcessBuilder(command).directory(directory.toFile()).redirectErrorStream(true);
        Map<String, String> ambiente = builder.environment();
        ambiente.keySet().removeIf(chave -> chave.equals("EMAIL")
                || chave.startsWith("GIT_AUTHOR_") || chave.startsWith("GIT_COMMITTER_"));
        Map<String, String> isolado = new HashMap<>();
        isolado.put("GIT_CONFIG_NOSYSTEM", "1");
        isolado.put("GIT_CONFIG_GLOBAL", GLOBAL_VAZIO.toString());
        isolado.put("XDG_CONFIG_HOME", XDG_VAZIO.toString());
        isolado.putAll(env);
        ambiente.putAll(isolado);
        Process process = builder.start();
        try (var output = process.getOutputStream()) {
            if (stdin != null) {
                output.write(stdin.getBytes(StandardCharsets.UTF_8));
            }
        }
        String result = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        return new Result(process.waitFor(), result);
    }

    private static Path criarGlobalVazio() {
        try {
            Path arquivo = Files.createTempFile("gitconfig-vazio", "");
            arquivo.toFile().deleteOnExit();
            return arquivo;
        } catch (java.io.IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    private static Path criarXdgVazio() {
        try {
            Path pasta = Files.createTempDirectory("xdg-vazio");
            pasta.toFile().deleteOnExit();
            return pasta;
        } catch (java.io.IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    private static String findBash() {
        String path = System.getenv().getOrDefault("PATH", "");
        for (String directory : path.split(Pattern.quote(System.getProperty("path.separator")))) {
            Path candidate = Path.of(directory, isWindows() ? "bash.exe" : "bash");
            if (Files.isExecutable(candidate)) {
                return candidate.toString();
            }
        }
        String programFiles = System.getenv("ProgramFiles");
        if (programFiles != null) {
            Path candidate = Path.of(programFiles, "Git", "bin", "bash.exe");
            if (Files.isRegularFile(candidate)) {
                return candidate.toString();
            }
        }
        return "bash";
    }

    private static boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("win");
    }

    private record Result(int exitCode, String output) {
    }
}