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

O padrão de estado é LOCALAPPDATA/CodexCopilot/hooks, separado do ledger de consultas e fora de sincronização. Não distribua esse banco. Há até 512 IDs de evento e 32 assinaturas de falha por tarefa, cinco lembretes por tarefa, entrada de até 128 KiB, SQLite com espera máxima de 100 ms e timeout de dois segundos por handler. A guarda de autorização não é dispensada quando o limite de avisos termina. Sem estado válido, ela recusa; lembretes falham sem alterar o trabalho. O banco pode crescer com o número de tarefas; não há limpeza destrutiva automática.

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

## Desativar/remover

O usuário pode desabilitar os handlers em /hooks. Para remover pelo pacote:

```text
python manage_hooks.py remove
python manage_hooks.py remove --apply
```

Use o mesmo --config do registro, se tiver personalizado. Remove somente as cinco definições exatas do recibo, preserva outros hooks e cria backup do arquivo atual. Se alguém as editou ou removeu parcialmente, a operação recusa alterações e pede inspeção manual; não restaura cegamente um backup antigo sobre configurações novas. Não apaga o estado nem backups automaticamente.

## Limites de segurança e validação

O hook reconhece apenas invocações literais simples do CLI. Variáveis de shell, chamadas Python indiretas, ferramentas fora da cobertura de hooks e uso direto de módulos não constituem uma barreira completa. A checagem no cliente/protocolo permanece necessária mesmo com hooks ausentes ou ignorados. O hash de escopo e o registro no ledger não substituem a autorização humana: nenhum hook interpreta conteúdo externo ou escolha do modelo como consentimento.

Os testes do pacote passam JSON sintético aos handlers e usam adaptadores falsos para impedir qualquer envio. Isso comprova as regras locais, sem comprovar eventos nativos ativos, payload de uma versão diferente do runtime ou consulta real. Somente marque um evento como observado quando o runtime efetivamente o produzir.

Referências verificadas: [Hooks](https://learn.chatgpt.com/docs/hooks), [empacotamento](https://developers.openai.com/plugins/build/plugins). O pacote permanece uma skill com configuração local opcional, sem criar plugin instalado, credenciais, conexão persistente ou publicação automática.
