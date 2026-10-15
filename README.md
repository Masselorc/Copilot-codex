# Copilot-codex

Skill e cliente Python para Codex consultar o aplicativo Microsoft 365 Copilot pela interface normal autenticada do Windows, exigindo Claude Opus 5.5. Copilot fornece texto e código; Codex decide o que aplicar ao projeto e testa o resultado.

Usa apenas a biblioteca padrão do Python e APIs públicas UI Automation/SendInput. Não precisa de Node.js, extensão, chave de API ou mudança de segurança. A interface em português foi testada com Python 3.14.7 de 64 bits. Outras versões do aplicativo, idiomas ou disponibilidade de modelo exigem verificação local.

## Instalação em outro Windows

1. Instale Python de 64 bits, versão 3.10 ou superior, pelos meios aprovados no computador de destino. Tenha Microsoft 365 Copilot instalado, aberto e autenticado normalmente, com Opus 5.5 disponível.
2. Baixe o repositório para uma pasta local e revise os arquivos. Na raiz, execute `python install_skill.py` para ver a simulação e o destino. Nada é instalado por esse comando.
3. Para copiar explicitamente a skill, execute `python install_skill.py --apply`. O destino padrão é `skills/copilot-consult` dentro de `CODEX_HOME`, se definido, ou de `~/.codex`. Para destino alternativo, use `--destination CAMINHO` tanto na simulação quanto em `--apply`; CAMINHO é a pasta final `copilot-consult`.
4. Para uma instalação nova, o instalador recusa destino existente, copia os 11 arquivos permitidos da skill e gera um `install-manifest.json` com seus hashes. Não configura Codex, abre Copilot ou migra autorizações. Confira o resumo e o `SKILL.md` no destino. Use o mecanismo de descoberta de skills do seu Codex para carregá-la na próxima sessão.

Antes de instalar, é possível conferir o cliente sem abrir aplicativos:

```powershell
python skill/copilot-consult/scripts/copilot.py --help
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

O instalador não instala Python nem o aplicativo e não concede autorização para consultas.

Para o caminho pessoal de descoberta `.agents/skills`, passe `--destination` explicitamente. Por exemplo, no PowerShell: `python install_skill.py --destination "$env:USERPROFILE/.agents/skills/copilot-consult"` para simular e o mesmo comando com `--apply` para instalar. Essa foi a localização validada pelo runtime do Codex; o padrão do instalador continua sendo `CODEX_HOME/skills` ou `~/.codex/skills`.

Para atualizar uma instalação gerenciada, acrescente `--update` à simulação e depois `--apply`. O instalador confere o manifesto anterior, recusa alterações locais nos arquivos gerenciados ou colisão com novos caminhos, preserva outros arquivos e cria backup fora do diretório de descoberta de skills. A atualização não registra nem confia em hooks.

## Gatilhos de consulta

A skill pode ser sugerida pelo agente conforme os [19 critérios definidos em SKILL.md](skill/copilot-consult/SKILL.md#gatilhos-de-consulta). Os cinco mais recentes cobrem execução demorada/cara, requisito sem comprovação, casos extremos esquecidos, viabilidade de instalação/uso/manutenção e retomada após perda de contexto. Os 14 critérios anteriores continuam presentes.

Esses critérios orientam o julgamento do agente; não há disparo automático por contagem ou tempo. Se ainda não houver autorização para consultas naquela tarefa, o agente explica a finalidade e pede autorização antes de transmitir conteúdo ao Copilot.

## Cinco hooks locais opcionais

A correção para timeouts no Windows usa limite externo de 10 segundos, sem aumentar a espera de SQLite (100 ms). authorize ignora chamadas irrelevantes antes de acessar estado; autorização e verificação inicial de custo usam somente leitura. Timeout/falha de hook não é garantia de bloqueio pelo runtime: a guarda independente do CLI/protocolo permanece obrigatória. Atualizar as definições antigas de 2 segundos exige nova revisão de confiança pelo usuário; o pacote não a concede.

O pacote inclui lembrete de início/retomada, aviso após duas falhas semelhantes identificáveis, guarda de autorização antes do CLI de envio, lembrete para comandos previamente marcados como caros e revisão final de tarefa extensa com uma única continuação. O [guia de hooks](skill/copilot-consult/references/hooks.md) descreve sinais, limites, vínculo por tarefa, preparação, registro, confiança e remoção.

Use `python manage_hooks.py prepare --skill-dir CAMINHO-DA-SKILL --output copilot-hooks.pending.json` para simular. Com `--apply`, grava somente a configuração pendente. `register --apply` é uma opção separada, com backup e preservação de outros hooks; depois o usuário precisa revisar/confiar nas definições em `/hooks`. O pacote nunca autoaprova confiança ou altera segurança. `python manage_hooks.py remove` simula a remoção, e `remove --apply` remove somente as definições exatas do recibo.

Hooks não enviam nada ao Copilot e não substituem a autorização humana nem a guarda do protocolo. `authorize` retorna `scope_sha256`; `query` e `resume` agora exigem `--scope-sha256` com esse valor. `revoke` encerra a autorização da tarefa. O hash identifica o escopo registrado, sem servir como prova de consentimento.

## Uso e privacidade

Leia [a skill](skill/copilot-consult/SKILL.md) e [a operação](skill/copilot-consult/references/operation.md). A autorização humana é exigida uma vez por tarefa, dentro do escopo aprovado. Anexos locais podem ser copiados para OneDrive corporativo/escolar: esse destino deve fazer parte da autorização. Comece com texto e arquivos sintéticos revisados.

Cada projeto/demanda mantém conversa própria; tarefas diferentes precisam de autorização própria. Todas as chamadas compartilham o mesmo diretório local de estado para serializar a interface. Não distribua banco, estado, autorizações ou respostas. Não use a janela ao mesmo tempo que a automação.

Em envio incerto, recupere a mesma request sem repetir com outro ID. Se modelo, login, limite ou interface impedirem a consulta, Codex continua nas partes independentes e informa o bloqueio. Não há troca silenciosa de modelo nem tentativa de contornar segurança.

Consulte [testes e limitações verificadas](docs/test-summary.md). Testes automatizados deste repositório são sintéticos e não enviam conteúdo ao Copilot. Geração estritamente simultânea em conversas distintas permanece inconclusiva.

## Conteúdo publicado

Código, skill, documentação sanitizada e testes unitários sintéticos. Evidências locais, capturas, conversas, estado, respostas reais e histórico de outros projetos não fazem parte deste pacote. Nenhuma licença foi acrescentada; a publicação não atribui por si só uma licença de uso.
