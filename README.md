# Copilot-codex

Skill e cliente Python para Codex consultar o aplicativo Microsoft 365 Copilot pela interface normal autenticada do Windows, exigindo Claude Opus 5.5. Copilot fornece texto e código; Codex decide o que aplicar ao projeto e testa o resultado.

Usa apenas a biblioteca padrão do Python e APIs públicas UI Automation/SendInput. Não precisa de Node.js, extensão, chave de API ou mudança de segurança. A interface em português foi testada com Python 3.14.7 de 64 bits. Outras versões do aplicativo, idiomas ou disponibilidade de modelo exigem verificação local.

## Instalação em outro Windows

1. Instale Python de 64 bits, versão 3.10 ou superior, pelos meios aprovados no computador de destino. Tenha Microsoft 365 Copilot instalado, aberto e autenticado normalmente, com Opus 5.5 disponível.
2. Baixe o repositório para uma pasta local e revise os arquivos. Na raiz, execute `python install_skill.py` para ver a simulação e o destino. Nada é instalado por esse comando.
3. Para copiar explicitamente a skill, execute `python install_skill.py --apply`. O destino padrão é `skills/copilot-consult` dentro de `CODEX_HOME`, se definido, ou de `~/.codex`. Para destino alternativo, use `--destination CAMINHO` tanto na simulação quanto em `--apply`; CAMINHO é a pasta final `copilot-consult`.
4. O instalador recusa destino existente, copia somente os sete arquivos da skill e gera um `install-manifest.json` com seus hashes. Não substitui versão instalada, configura Codex, abre Copilot ou migra autorizações. Confira o resumo e o `SKILL.md` no destino. Use o mecanismo de descoberta de skills do seu Codex para carregá-la na próxima sessão.

Antes de instalar, é possível conferir o cliente sem abrir aplicativos:

```powershell
python skill/copilot-consult/scripts/copilot.py --help
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

O instalador não instala Python nem o aplicativo e não concede autorização para consultas.

Para o caminho pessoal de descoberta `.agents/skills`, passe `--destination` explicitamente. Por exemplo, no PowerShell: `python install_skill.py --destination "$env:USERPROFILE/.agents/skills/copilot-consult"` para simular e o mesmo comando com `--apply` para instalar. Essa foi a localização validada pelo runtime do Codex; o padrão do instalador continua sendo `CODEX_HOME/skills` ou `~/.codex/skills`.

## Gatilhos de consulta

A skill pode ser sugerida pelo agente conforme os [14 critérios definidos em SKILL.md](skill/copilot-consult/SKILL.md#gatilhos-de-consulta). Aos oito critérios iniciais foram acrescentados: incerteza relevante, divergência entre testes e comportamento real, correção sem causa identificada, mudança difícil de reverter, impacto em segurança ou integridade e verificação final de tarefa extensa.

Esses critérios orientam o julgamento do agente; não há disparo automático por contagem ou tempo. Se ainda não houver autorização para consultas naquela tarefa, o agente explica a finalidade e pede autorização antes de transmitir conteúdo ao Copilot.

## Uso e privacidade

Leia [a skill](skill/copilot-consult/SKILL.md) e [a operação](skill/copilot-consult/references/operation.md). A autorização humana é exigida uma vez por tarefa, dentro do escopo aprovado. Anexos locais podem ser copiados para OneDrive corporativo/escolar: esse destino deve fazer parte da autorização. Comece com texto e arquivos sintéticos revisados.

Cada projeto/demanda mantém conversa própria; tarefas diferentes precisam de autorização própria. Todas as chamadas compartilham o mesmo diretório local de estado para serializar a interface. Não distribua banco, estado, autorizações ou respostas. Não use a janela ao mesmo tempo que a automação.

Em envio incerto, recupere a mesma request sem repetir com outro ID. Se modelo, login, limite ou interface impedirem a consulta, Codex continua nas partes independentes e informa o bloqueio. Não há troca silenciosa de modelo nem tentativa de contornar segurança.

Consulte [testes e limitações verificadas](docs/test-summary.md). Testes automatizados deste repositório são sintéticos e não enviam conteúdo ao Copilot. Geração estritamente simultânea em conversas distintas permanece inconclusiva.

## Conteúdo publicado

Código, skill, documentação sanitizada e testes unitários sintéticos. Evidências locais, capturas, conversas, estado, respostas reais e histórico de outros projetos não fazem parte deste pacote. Nenhuma licença foi acrescentada; a publicação não atribui por si só uma licença de uso.
