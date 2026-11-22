# install-githooks

Instalador global de hooks para Windows (Git Bash), Linux e macOS. Requer Bash e Git 2.29 ou superior.

```bash
bash install.sh
bash install.sh --scan
```

O instalador configura `core.hooksPath` no Git global e preserva cópias dos hooks globais que substituir. Hooks locais em `.git/hooks` continuam sendo delegados quando o repositório não define seu próprio `core.hooksPath`.

As travas verificam mensagens e coautoria de commits, nomes de referências e commits enviados. O hook `reference-transaction` também rejeita referências e mensagens proibidas mesmo com `git commit --no-verify`, `--amend`, rebase, cherry-pick e merge. No Git anterior a 2.29 essa garantia não está disponível.

`reference-transaction` e `pre-push` só verificam a mensagem de commits cujo committer é a identidade que o Git usaria para um commit novo naquele repositório (`git var GIT_COMMITTER_IDENT`). Commits de terceiros recebidos por clone, fetch ou pull passam. As referências que só espelham o remoto (`refs/remotes/*`, `FETCH_HEAD` e `ORIG_HEAD`) não são verificadas. Se o Git não conseguir determinar a identidade, todos os commits são verificados.

Para procurar configurações locais que sobrepõem os hooks globais:

```bash
bash scripts/scan-custom-hooks.sh "$HOME"
bash scripts/scan-custom-hooks.sh "$HOME" /c/ /d/
```

Uma configuração `core.hooksPath` no próprio repositório tem precedência sobre a global. O scanner mostra a origem e o valor para revisão manual.

## Hooks versionados do projeto

Para os hooks globais rodarem antes dos hooks versionados de um projeto, troque o `core.hooksPath` local pela chave `hooks.versionado`:

```bash
git config --local --unset core.hooksPath
git config --local hooks.versionado .githooks
```

O `_delegar` executa, nesta ordem, o hook da pasta em `hooks.versionado` e o hook em `.git/hooks`, e para na primeira falha. Sem a chave, a pasta versionada não roda. Assim, um clone de terceiro não executa código nesta máquina. O scanner mostra os dois comandos para cada repositório com `core.hooksPath` local.

## Repos só leitura

Cada máquina pode travar repositórios que só recebem dados. A lista é particular: fica em `~/.config/git/somente-leitura.lista`, fora de qualquer repositório, e o `install.sh` nunca a sobrescreve.

```bash
~/.config/git/somente-leitura adicionar
~/.config/git/somente-leitura adicionar url:https://git.exemplo.com/terceiro/
~/.config/git/somente-leitura adicionar pasta:~/projetos/arquivo-morto
~/.config/git/somente-leitura listar
```

Sem argumento, `adicionar` trava o repositório da pasta atual pela URL do remoto `origin`, sem usuário e senha. Uma entrada `url:` trava todo remoto que começa com o prefixo. Uma entrada `pasta:` trava os repositórios dentro da pasta.

Num repositório travado:

- O `~/.gitconfig` ganha uma regra `url."somente-leitura://bloqueado/".pushInsteadOf` para cada entrada `url:`. O push falha mesmo com `--no-verify` ou sem hooks.
- O `pre-push` recusa o envio com a entrada que travou o repositório.
- O `reference-transaction` recusa commit que não veio do remoto, branch apagada e branch que volta ou é reescrita. Isso vale também com `--no-verify` e em worktree do repositório.
- Fetch, pull em fast-forward, clone e branch criada a partir de uma ref remota continuam funcionando.

Para destravar, execute `~/.config/git/somente-leitura remover <entrada>` no terminal e digite `sim`. Sem terminal, o comando recusa. Um agente de IA consegue travar, mas não destravar.

A trava fica na conta do usuário da máquina. Quem muda a configuração do Git de propósito, por exemplo com `git -c core.hooksPath=/dev/null` e uma regra `pushInsteadOf` mais longa, passa por ela. A garantia contra isso é a permissão no servidor: um token só de leitura ou um papel só de leitura no repositório.

## Limites

Hooks locais não conseguem validar título ou descrição de PR criado pelo site ou API. O `pre-push` valida branches e mensagens dos commits próprios; validação dos metadados do PR precisa ser configurada no provedor Git.

## Testes

Os testes usam Spring Boot Test, JUnit 5, Git e Bash. Eles criam repositórios temporários e não alteram a configuração Git da máquina:

```bash
mvn test
```
