# install-githooks

Instalador global de hooks para Windows (Git Bash), Linux e macOS. Requer Bash e Git 2.29 ou superior.

```bash
bash install.sh
bash install.sh --scan
```

O instalador configura `core.hooksPath` no Git global e preserva cópias dos hooks globais que substituir. Hooks locais em `.git/hooks` continuam sendo delegados quando o repositório não define seu próprio `core.hooksPath`.

As travas verificam mensagens e coautoria de commits, nomes de referências e commits enviados. O hook `reference-transaction` também rejeita referências e mensagens proibidas mesmo com `git commit --no-verify`, `--amend`, rebase, cherry-pick e merge. No Git anterior a 2.29 essa garantia não está disponível.

`reference-transaction` e `pre-push` só verificam a mensagem de commits cujo committer é a identidade que o Git usaria para um commit novo naquele repositório (`git var GIT_COMMITTER_IDENT`). Commits de terceiros recebidos por clone, fetch ou pull passam. As referências que só espelham o remoto (`refs/remotes/*`, `FETCH_HEAD` e `ORIG_HEAD`) não são verificadas. Se o Git não conseguir determinar a identidade, todos os commits são verificados.

O `pre-commit` verifica o nome de cada arquivo do commit e as linhas adicionadas. Ele usa dois níveis de termos de IA, e a lista de cada nível está em `hooks/_bloquear_termos_ia_conteudo`:

- Termos fortes: nomes de empresa, de produto e de assistente de IA que não têm outro uso em código. O hook barra esses termos em qualquer arquivo.
- Termos ambíguos: nomes de ferramenta e de modelo de IA que também são palavras comuns em código, como a propriedade CSS do ponteiro do mouse ou o cursor do SQL. O hook barra esses termos só em arquivo de texto (`.md`, `.markdown`, `.mdx`, `.txt`, `.rst` e `.adoc`).

Por isso a propriedade CSS do ponteiro passa num `.scss` ou num `.vue`, mas o nome do editor de IA homônimo é barrado num `.md`. A extensão do arquivo decide o nível, tanto para o nome quanto para o conteúdo. A checagem da mensagem de commit não muda: ela barra os dois níveis em qualquer mensagem.

Para isentar um caminho num repositório, use um padrão de caminho:

```bash
git config --local --add hooks.termosIaIgnorar 'caminho/ou/padrao/*'
```

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

## Trava git do agente de IA

Os hooks do Git não veem o `gh`, porque o Git não chama hook nessa ferramenta. Para cobrir o `gh` e o envio ao remoto quando um agente de IA executa comandos, o projeto tem o `agentes/git_guard.py`. Ele é um hook PreToolUse do agente: antes de cada comando Bash, ele confere os comandos `git` e `gh`.

- Comandos que só leem ou que gravam só na máquina passam direto, por exemplo `git status`, `git fetch`, `git pull`, `git commit`, `git reset`, `gh pr view`, `gh auth token` e `gh repo clone`.
- Comandos que enviam algo para o remoto pedem a sua aprovação: `git push` (inclusive o que apaga branch ou tag remota), `git send-email` e todo `gh` que altera o remoto, como `gh pr create` e `gh api -X POST`. Também pedem aprovação `gh auth login`, `gh auth refresh` e `gh auth logout`, porque eles mudam a credencial do GitHub.

O `install.sh` instala a trava com o script `agentes/instalar`:

1. O script procura a pasta de configuração do agente na sua pasta pessoal, ou na pasta que a variável de ambiente do agente indica. Os detalhes estão no cabeçalho de `agentes/instalar`.
2. Se a pasta não existe, o agente não está instalado e nada muda.
3. Se a pasta existe, o script copia o hook para a subpasta `hooks` e registra uma única entrada no `settings.json`. As outras configurações do arquivo continuam como estão.
4. Antes de gravar, o script faz um backup do `settings.json`. Se o arquivo não é um JSON válido, o script não grava nada e avisa.

A trava precisa de Python 3.8 ou superior. O `install.sh` testa `python3`, `python` e `py`, nessa ordem, e grava no registro o primeiro que funciona. Assim o mesmo comando roda no macOS, no Linux e no Git Bash do Windows. Sem Python, os hooks do Git são instalados e a trava do agente fica de fora, com um aviso.

O `git_guard.py` é a única trava de git e gh do agente. As regras de aprovação de git e gh saem do `settings.json` do agente, porque uma regra de aprovação ali vence a liberação que o hook dá.

### Lista de autonomia

A lista de autonomia libera o agente de IA em projetos escolhidos. A lista é particular desta máquina e fica em `~/.config/git/autonomia.lista`, ou em `$XDG_CONFIG_HOME/git/autonomia.lista` quando essa variável existe. As entradas usam o formato da lista só leitura: `url:` ou `pasta:`. O script `~/.config/git/autonomia` mantém a lista:

```bash
~/.config/git/autonomia adicionar
~/.config/git/autonomia adicionar pasta:~/projetos/meu-projeto
~/.config/git/autonomia listar
~/.config/git/autonomia remover <entrada>
```

No Windows, o Git Bash é requisito: execute o script `autonomia` no Git Bash. O script grava a pasta no formato do bash (`/c/Users/...`), e a trava converte esse caminho para `C:/Users/...` e compara sem diferenciar maiúsculas e minúsculas. A trava também aceita os formatos do WSL (`/mnt/c/...`) e do Cygwin (`/cygdrive/c/...`).

Num projeto da lista, todo git e gh que pede aprovação passa direto. Isso inclui `git push --force`, `gh pr merge`, `gh release create` e `gh repo delete` do próprio repositório. Uma entrada `url:` vale só se todo remoto do repositório começa com o prefixo. Uma entrada `pasta:` vale para todo remoto do repositório.

A liberação vale só para o comando em que cada parte roda git, gh ou um destes comandos: `echo`, `printf`, `cat`, `true`, `test`, `[`, `head`, `tail`, `wc`, `grep`, `ls` e `pwd`. Isso vale também para o comando dentro de `$(...)`, de crase e de here-doc. Um redirecionamento que grava arquivo, `sudo`, qualquer `git config` (também o de leitura) ou outro comando na mesma linha, como `git push origin main && rm -rf x`, tira a liberação. Nesse caso, a trava não decide nada, e as regras de permissão do agente decidem, como para qualquer outro comando.

Mesmo num projeto da lista, estes comandos continuam pedindo aprovação:

- Comando que muda de repositório, como `cd`, `pushd`, `popd`, `git -C`, `git -c`, `--git-dir`, `--work-tree`, `git remote set-url`, `git config` com URL, as variáveis `GIT_*`, `GH_REPO` e `GH_HOST`, `xargs`, `gh -R` e `gh --repo`.
- `git push` para destino que não é remoto nomeado, como um caminho ou uma URL.
- URL ou `owner/repo` de outro repositório na posição em que o gh espera um repositório.
- Alvo em variável, ou substituição com `$` ou crase, no comando que pede aprovação.
- gh do escopo da conta, como `gh auth`, `gh extension`, `gh gist` e `gh ssh-key`.
- Todo comando que cita a lista ou que executa `autonomia adicionar`.

A lista só leitura vence a lista de autonomia. Num repositório que está nas duas listas, o agente sempre pede aprovação, e os hooks do Git ainda recusam o envio.

O `adicionar` só funciona no terminal e pede que você digite `sim`. O agente de IA nunca deve pôr um projeto na lista. Tirar da lista não pede confirmação.

A lista tem limites conhecidos:

- A trava da lista confere o texto do comando. Um comando que monta o nome da lista numa variável passa por ela.
- As ferramentas do agente que editam e gravam arquivos não passam pelo hook. Por isso o `settings.json` do agente nega essas ferramentas no arquivo da lista.

## Limites

Hooks locais não conseguem validar título ou descrição de PR criado pelo site ou API. O `pre-push` valida branches e mensagens dos commits próprios; validação dos metadados do PR precisa ser configurada no provedor Git.

## Testes

Os testes usam Python 3, Git e Bash, com o módulo `unittest` da biblioteca padrão. Não há dependência para instalar. Os testes criam repositórios temporários e não alteram a configuração Git da máquina. Execute na raiz do projeto:

```bash
python3 -m unittest discover -s tests -v
```

No Windows, execute o mesmo comando no Git Bash. Se o Python não responde por `python3`, use `py` no lugar.
