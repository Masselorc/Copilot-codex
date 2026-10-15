# Operação no Windows

Python de 64 bits, versão 3.10 ou superior, sem dependências externas. A execução real foi verificada com Python 3.14.7 e Microsoft 365 Copilot em português. O aplicativo precisa estar aberto na sessão interativa, autenticado pelo próprio usuário, com Opus 5.5 disponível. Não há suporte implementado para navegador, outras interfaces/idiomas ou seleção aproximada de controles.

## Comandos

Na raiz do repositório, estes comandos PowerShell apenas iniciam Python; não executam arquivo `.ps1` nem exigem mudança de ExecutionPolicy:

```powershell
$cli = 'skill/copilot-consult/scripts/copilot.py'
python $cli --help
python $cli doctor

# Somente depois de autorização humana real nesta tarefa:
$grant = python $cli authorize --project . --demand revisao-exemplo --task tarefa-atual --evidence 'Autorização nesta tarefa' --scope 'Revisão técnica com texto sintético' --synthetic-only | ConvertFrom-Json

# Prepare consulta.txt em UTF-8 com conteúdo sintético revisado.
python $cli query --project . --demand revisao-exemplo --task tarefa-atual --request consulta-01 --prompt-file consulta.txt --scope-sha256 $grant.scope_sha256 --synthetic --wait 35
python $cli recover --project . --demand revisao-exemplo --task tarefa-atual --request consulta-01 --wait 35
python $cli status --project . --demand revisao-exemplo --task tarefa-atual --request consulta-01

# Para revogar a autorização desta tarefa:
python $cli revoke --project . --demand revisao-exemplo --task tarefa-atual --reason 'Revogação solicitada pelo usuário'
```

Após instalação, substitua `$cli` pelo caminho de `scripts/copilot.py` no destino mostrado pelo instalador. `doctor` não envia conteúdo, mas seu JSON inclui o ID da conversa visível: mantenha-o privado. `authorize`, `revoke`, `status` e `--help` não precisam abrir a interface. `query`, `resume` e `recover` precisam dela; `recover` não envia. O hash retornado por `authorize` deve ser informado também em `resume`; nunca o gere a partir de um escopo diferente para tentar liberar envio.

O cliente confere autorização, escopo e revogação antes de inicializar a interface para query/resume e o protocolo reconfere antes do envio. Revogação durante preparação impede envio; se a ação já tiver ocorrido, revogar não a desfaz e deve-se somente recuperar o estado. Hooks opcionais adicionam outra conferência, com limites descritos em [hooks](hooks.md).

Para anexar, acrescente `--attach arquivo.txt`, repetível. Autorize previamente a cópia no OneDrive corporativo/escolar. TXT e PNG foram testados com conteúdo sintético; outras extensões exigem verificação própria. `--synthetic-only` exige `--synthetic` nas consultas, mas essas opções não detectam conteúdo sensível. Revise os arquivos antes do envio.

Não edite os arquivos durante a operação. Nomes de anexos duplicados são recusados, inclusive com diferenças apenas de maiúsculas. O cliente reconfere hashes locais antes da preparação e do envio e confere os chips exibidos. A interface não permite comprovar os bytes já carregados no servidor: essas verificações não substituem manter arquivos estáveis e impedir intervenção humana concorrente. Uma alteração durante o upload pode ser detectada somente depois de o aplicativo já ter copiado o arquivo; nesse caso o pedido não é enviado, mas o upload pode ter ocorrido.

## Estado, identidade e lock

O padrão é `%LOCALAPPDATA%/CodexCopilot`. Uma alternativa deve ser passada antes do subcomando: `python $cli --state-dir CAMINHO query ...`. Todos os projetos, demandas e processos da mesma sessão precisam compartilhar esse diretório para compartilhar `copilot-ui.lock`. Não sincronize nem distribua o estado: ele contém autorizações, caminhos de projetos, IDs de conversas, respostas e metadados. Não armazena o texto do prompt no ledger, mas armazena seus hashes.

A autorização pertence a `(projeto, demanda, tarefa)`. A conversa pertence a `(projeto, demanda)` e seu ID não pode pertencer a outro par. A request pertence a `(projeto, demanda, request)` e não pode ser adotada por outra tarefa. Use nova demanda quando precisar de contexto independente; mudar apenas a tarefa mantém a conversa da demanda, mas exige nova autorização.

O lock abrange navegação, foco, teclado, envio e espera. Não protege contra intervenção humana nem chamadas diretas aos módulos fora do protocolo. Não há scheduler paralelo. Não use a janela enquanto o driver estiver ativo.

## Recuperação

- `COMPLETE`: texto exposto pela interface, com sinais de conclusão e duas leituras estáveis; repetição retorna o resultado armazenado.
- `PREPARING`: confira primeiro com `recover`. `resume`, com os mesmos argumentos de `query`, só continua após verificação de ausência de envio e rascunho próprio de texto sem anexos ou editor vazio sem anexos. Chips de upload existentes bloqueiam a retomada mesmo com nomes iguais: seu conteúdo não pode ser comprovado. São preservados para inspeção, sem remoção ou reupload automático.
- `DISPATCHING` ou `UNKNOWN`: o envio pode ter ocorrido; use somente `recover`. Nunca gere outro ID para reenviar o mesmo pedido.
- `BLOCKED`: examine motivo, estado e interface. Não altere o banco manualmente para liberar envios. Em limite de uso ou login, continue o trabalho sem Copilot.

O banco registra `DISPATCHING` antes da ação física, usa transações e sincronização completa e mantém eventos de auditoria sem edição. Uma falha entre clique e observação pode deixar resultado desconhecido: não existe garantia de entrega exatamente uma vez. O cliente limita a uma tentativa de envio por request; a exceção específica anterior ao clique permite retomar preparação sem apagar o histórico.

Guardas locais: até 10 requests por tarefa/demanda, 10 anexos e 10 MiB por pedido, texto de até 24.000 caracteres e espera de leitura de até 55 segundos por chamada. São precauções do cliente, não limites oficiais do Copilot.

## Leitura e limites

Controles são localizados por nome/ID e padrões UIA. Cliques são derivados do retângulo do controle recém-observado, sem coordenadas fixas; entrada normal usa SendInput, sem clipboard. Mudanças da interface interrompem a ação.

A ação Novo chat é procurada somente dentro do documento Copilot validado por domínio e rota de chat. Na interface observada, essa ação é Hyperlink com nome Novo chat e ID vazio; uma conversa do histórico pode ter o mesmo título, mas possui ID de conversa. O seletor exige a identidade observada completa e exatamente um resultado, sem escolher primeiro/último, usar posição ou cair em uma entrada de histórico. Dois controles ainda compatíveis, ausência da ação, ID alterado ou documento inesperado interrompem sem navegação. Uma futura mudança da interface pode exigir nova validação; não relaxe a unicidade para forçar a operação.

Erros de unicidade agora informam os critérios solicitados e a contagem, sem listar nomes/valores dos candidatos ou conteúdo de conversas. A identificação da colisão e a correção do seletor não provam se uma tentativa anterior transmitiu conteúdo. Corrigir a interface não altera UNKNOWN, não cria nova autorização e não permite repetir uma request incerta; mantenha somente recuperação/leitura dentro do escopo aprovado.

TextPattern usa `GetText(-1)` no corpo da resposta. Blocos de código na estrutura observada são reconstruídos pelos pares número/conteúdo de linha, preservando indentação; lacunas e estruturas desconhecidas são recusadas. Ícones/citações podem aparecer como U+FFFC. Tabelas podem perder apresentação visual; código/respostas muito longos ou virtualizados ainda precisam de verificação específica. Não conclua que nada foi enviado pela ausência de texto.

Geração estritamente simultânea em duas conversas não foi comprovada. Foi possível alternar e recuperar resultados finais isolados; a árvore UIA invalidou-se transitoriamente ao alternar. Uma segunda abertura do aplicativo reutilizou a janela. O CLI permanece conservador e serial.

Referências da implementação COM: [IUIAutomation](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomation) e [header do Windows SDK](https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/UIAutomationClient.h). As posições da vtable seguem o header.
