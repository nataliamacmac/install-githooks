package br.com.installgithooks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
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
        assertEquals(0, git(repo, "init").exitCode());
        git(repo, "config", "user.name", "Teste Local");
        git(repo, "config", "user.email", "teste@example.invalid");
        git(repo, "config", "core.hooksPath", HOOKS.toString().replace('\\', '/'));
        Result base = git(repo, "commit", "--allow-empty", "-m", "commit inicial limpo");
        assertEquals(0, base.exitCode(), base.output());
        return repo;
    }

    private static Result git(Path repo, String... args) throws Exception {
        List<String> command = new ArrayList<>();
        command.add("git");
        command.add("-C");
        command.add(repo.toString());
        command.addAll(List.of(args));
        return executar(repo, null, Map.of(), command.toArray(String[]::new));
    }

    private static Result executar(Path directory, String stdin, Map<String, String> env, String... command) throws Exception {
        ProcessBuilder builder = new ProcessBuilder(command).directory(directory.toFile()).redirectErrorStream(true);
        builder.environment().putAll(env);
        Process process = builder.start();
        try (var output = process.getOutputStream()) {
            if (stdin != null) {
                output.write(stdin.getBytes(StandardCharsets.UTF_8));
            }
        }
        String result = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        return new Result(process.waitFor(), result);
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