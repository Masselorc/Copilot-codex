# Hooks opcionais no Codex

Os 19 critérios continuam sendo julgados pelo agente. Cinco regras locais auxiliam o fluxo; elas não consultam Copilot, não leem transcrições, não criam autorização e não invocam outros processos. O transporte permanece no cliente Python, com a verificação do escopo e de revogação antes do envio.

## Regras e sinais observáveis

| Regra | Evento | Condição e efeito |
| --- | --- | --- |
| Início/retomada | SessionStart | Lembrete curto, uma vez no início e uma na família de retomada/compactação por tarefa. Sem vínculo, no máximo dois por sessão. |
| Falhas semelhantes | PostToolUse | Dois tool_use_id distintos, na mesma etapa, com mesma ferramenta, comando normalizado e código reconhecido. Emite um lembrete por tarefa. |
| Autorização | PreToolUse | Para query/resume literais de copilot.py, confere vínculo de sessão/tarefa, diretório de estado, projeto/demanda/tarefa e hash do escopo contra o ledger local, incluindo revogação. Ausência ou divergência retorna deny; sucesso não autoaprova a chamada. |
| Custo/duração | PreToolUse | Comando exato previamente marcado como caro/demorado no vínculo local. Um lembrete por tarefa para avaliar o plano. Não estima custos por palavras soltas. |
| Revisão final | Stop | Somente tarefa explicitamente extensa ou com pelo menos 20 eventos de ferramenta distintos observados. Uma única continuação por tarefa; stop_hook_active true, campo ausente, reentrega ou retomada após emissão não geram nova continuação. |

A contagem reconhece somente: Bash/exec_command com objeto exit_code inteiro; string JSON equivalente; envelope textual com linha explícita “Process exited with code N”, “Exit code: N” ou “Process exit code: N”; MCP com isError true. Outros formatos são desconhecidos e não contam. Não existe evento PostToolUseFailure neste pacote. Cancelamento, interrupção e recusa de permissão/acesso são excluídos conservadoramente. Texto arbitrário “erro” não prova falha.

Eventos de copilot.py e dos scripts auxiliares são excluídos dos lembretes e contadores, mas query/resume continuam sujeitos ao hook de autorização. Falhas de execução do próprio bridge não disparam repetição automática.

## Vínculo por tarefa

O runtime fornece session_id e cwd; registre o vínculo antes de depender dos contadores. O ID da tarefa é a mesma identidade usada na autorização do cliente. Não descubra IDs lendo históricos; use os metadados disponíveis na tarefa ou um evento fornecido pelo runtime. Sem ID confiável, os lembretes ficam limitados e o envio identificado é recusado.

```text
python skill/copilot-consult/scripts/hook_admin.py --session ID-DA-SESSAO --cwd CAMINHO-DA-SESSAO --project PROJETO --demand DEMANDA --task TAREFA --stage ETAPA
```

Acrescente --extensive apenas se a tarefa for extensa. Para marcar um comando caro, grave somente seu texto em arquivo local e use --expensive-command-file ARQUIVO; pode repetir a opção até 16 vezes. A ferramenta armazena hashes dos comandos, não seu texto. Trocar a etapa separa os contadores; trocar de tarefa exige novo vínculo. Retomar a mesma tarefa preserva os limites, a contagem e a guarda Stop; isso não transfere nem renova autorização.

O padrão de estado é LOCALAPPDATA/CodexCopilot/hooks, separado do ledger de consultas e fora de sincronização. Não distribua esse banco. Há até 512 IDs de evento e 32 assinaturas de falha por tarefa, cinco lembretes por tarefa, entrada de até 128 KiB, SQLite com espera máxima de 100 ms e timeout de dez segundos por handler. O limite inclui a margem de lançamento do processo no Windows; não é uma espera interna obrigatória. A guarda de autorização não é dispensada quando o limite de avisos termina. Sem estado válido, ela retorna deny; lembretes falham sem alterar o trabalho. O banco pode crescer com o número de tarefas; não há limpeza destrutiva automática.

Chamadas que não contêm query/resume de copilot.py retornam antes de abrir estado no hook authorize. Autorizações usam somente leitura do estado e do ledger; comandos não marcados para custo usam leitura sem transação de escrita. A inicialização do schema fica no vínculo administrativo e em SessionStart. Só alterações reais de contadores e avisos usam transação de escrita, com reconferência para evitar perda de estado concorrente.

## Preparar, registrar e confiar

Na raiz do pacote:

```text
python manage_hooks.py prepare --skill-dir CAMINHO-DA-SKILL-INSTALADA --output copilot-hooks.pending.json
python manage_hooks.py prepare --skill-dir CAMINHO-DA-SKILL-INSTALADA --output copilot-hooks.pending.json --apply
```

O primeiro comando é uma simulação. O segundo escreve apenas a configuração pendente, sem registrá-la ou executá-la. Os caminhos são gerados para a máquina de destino; não copie configurações geradas entre máquinas. O comando usa Python disponível no PATH, sem Node, extensões ou script PowerShell.

Para registrar opcionalmente, depois de revisar a configuração e obter autorização para esse efeito:

```text
python manage_hooks.py register --skill-dir CAMINHO-DA-SKILL-INSTALADA
python manage_hooks.py register --skill-dir CAMINHO-DA-SKILL-INSTALADA --apply
```

Registro mescla os cinco handlers no hooks.json escolhido, preserva definições e metadados existentes e cria backup/recibo local. Não escreve config.toml nem confiança. Repetir registro idêntico não duplica handlers. Definições modificadas devem ser removidas/revisadas antes de substituir.

Antes de rodar hooks não gerenciados, Codex exige revisão e confiança da definição exata pelo usuário em /hooks. Não use flags de bypass, não marque hooks como gerenciados e não edite registros de confiança. Se essa revisão estiver pendente, os hooks não estão ativos. O aplicativo que hospeda a tarefa não deve ser reiniciado automaticamente.

A correção de timeout altera as cinco definições de 2 para 10 segundos: a confiança anterior não aprova automaticamente esta versão. Para substituir um registro anterior, simule e aplique remove com o recibo existente, depois simule e aplique register com a skill atualizada. Ambos preservam outros hooks e criam backup. Revise as novas definições em /hooks; o pacote não aprova confiança.

## Desativar/remover

O usuário pode desabilitar os handlers em /hooks. Para remover pelo pacote:

```text
python manage_hooks.py remove
python manage_hooks.py remove --apply
```

Use o mesmo --config do registro, se tiver personalizado. Remove somente as cinco definições exatas do recibo, preserva outros hooks e cria backup do arquivo atual. Se alguém as editou ou removeu parcialmente, a operação recusa alterações e pede inspeção manual; não restaura cegamente um backup antigo sobre configurações novas. Não apaga o estado nem backups automaticamente.

## Limites de segurança e validação

O hook reconhece apenas invocações literais simples do CLI. Variáveis de shell, chamadas Python indiretas, ferramentas fora da cobertura de hooks e uso direto de módulos não constituem uma barreira completa. A checagem no cliente/protocolo permanece necessária mesmo com hooks ausentes ou ignorados. O hash de escopo e o registro no ledger não substituem a autorização humana: nenhum hook interpreta conteúdo externo ou escolha do modelo como consentimento.

Uma recusa JSON explícita é diferente de um timeout ou falha de processo. Não suponha que erro/timeout de PreToolUse bloqueará a ferramenta: o runtime pode continuar. O handler retorna deny quando identifica envio e não consegue verificar estado; o CLI verifica autorização antes de importar/construir a UI, e o protocolo reconfere o escopo antes do envio. Ausência, revogação, divergência ou banco bloqueado impedem esse caminho de envio mesmo sem hook. Não se trata de uma barreira contra uso direto de módulos ou alteração local do cliente.

Hooks de comando locais exigem orquestração local. A documentação oficial não suporta esses handlers em orquestração cloud, mesmo quando ferramentas executam no computador. Portanto, uma tarefa delegada com execução local não comprova o disparo do cliente Codex nativo. Observe o histórico nativo e diferencie catálogo, evento concluído e efeito da regra.

Os testes do pacote passam JSON sintético aos handlers e usam adaptadores falsos para impedir qualquer envio. Isso comprova as regras locais, sem comprovar eventos nativos ativos, payload de uma versão diferente do runtime ou consulta real. Somente marque um evento como observado quando o runtime efetivamente o produzir.

Referências verificadas: [Hooks](https://learn.chatgpt.com/docs/hooks), [empacotamento](https://developers.openai.com/plugins/build/plugins). O pacote permanece uma skill com configuração local opcional, sem criar plugin instalado, credenciais, conexão persistente ou publicação automática.
