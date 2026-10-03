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