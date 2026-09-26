# install-githooks

Instalador global de hooks para Windows (Git Bash), Linux e macOS. Requer Bash e Git 2.29 ou superior.

```bash
bash install.sh
bash install.sh --scan
```

O instalador configura `core.hooksPath` no Git global e preserva cópias dos hooks globais que substituir. Hooks locais em `.git/hooks` continuam sendo delegados quando o repositório não define seu próprio `core.hooksPath`.

As travas verificam mensagens e coautoria de commits, nomes de referências e commits enviados. O hook `reference-transaction` também rejeita referências e mensagens proibidas mesmo com `git commit --no-verify`. No Git anterior a 2.29 essa garantia não está disponível.

Para procurar configurações locais que sobrepõem os hooks globais:

```bash
bash scripts/scan-custom-hooks.sh "$HOME"
bash scripts/scan-custom-hooks.sh "$HOME" /c/ /d/
```

Uma configuração `core.hooksPath` no próprio repositório tem precedência sobre a global. O scanner mostra a origem e o valor para revisão manual.

Hooks locais não conseguem validar título ou descrição de PR criado pelo site ou API. O `pre-push` valida branches e mensagens dos commits; validação dos metadados do PR precisa ser configurada no provedor Git.

## Testes

Os testes usam Spring Boot Test, JUnit 5, Git e Bash. Eles criam repositórios temporários e não alteram a configuração Git da máquina:

```bash
mvn test
```
