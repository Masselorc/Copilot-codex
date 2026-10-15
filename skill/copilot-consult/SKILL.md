---
name: copilot-consult
description: Consultar Microsoft 365 Copilot no Windows como apoio ao Codex em problemas complexos, tentativas fracassadas, sínteses volumosas, comparação de alternativas, erros recorrentes, contradições documentais, decisões que afetam várias partes, revisão independente, incerteza relevante, divergência entre testes e comportamento real, correção sem causa identificada, mudanças difíceis de reverter, impacto em segurança ou integridade e verificação final de tarefa extensa. Usa a interface autenticada e exige Claude Opus 5.5.
---

Use `scripts/copilot.py` para controlar o aplicativo Microsoft 365 Copilot pela interface normal do Windows. Leia [operação e limites](references/operation.md) antes da primeira consulta. O transporte usa Python e UI Automation/SendInput públicos, sem Node.js, API privada, cookies, tokens, DevTools ou extensões.

## Gatilhos de consulta

Considere propor uma consulta quando uma segunda análise puder contribuir para uma destas situações:

1. **Problema complexo.**
2. **Tentativa fracassada de solução.**
3. **Síntese de conteúdo volumoso.**
4. **Comparação de alternativas.**
5. **Erro recorrente.**
6. **Contradição documental.**
7. **Decisão que afeta várias partes do projeto.**
8. **Necessidade de revisão independente.**
9. **Incerteza relevante:** a solução depende de uma hipótese central pouco sustentada.
10. **Divergência entre testes e comportamento real:** os testes passam, mas o problema continua, ou a falha é intermitente.
11. **Correção sem causa identificada:** a alteração elimina o sintoma, mas o agente não consegue explicar por quê.
12. **Mudança difícil de reverter:** migração de dados, alteração de formatos persistidos ou quebra de compatibilidade.
13. **Impacto em segurança ou integridade:** mudança relevante em autenticação, permissões ou tratamento de dados.
14. **Verificação final de tarefa extensa:** conferir requisitos esquecidos, efeitos colaterais e lacunas nos testes antes de declarar conclusão.

São critérios para julgamento do agente, sem disparos automáticos por contagem ou tempo. Ao sugerir uma consulta, explique o motivo e a contribuição esperada. A presença de um gatilho não autoriza transmitir conteúdo: siga a autorização por tarefa descrita abaixo.

## Autorização

Antes da primeira consulta de cada tarefa, verifique se o usuário já autorizou consultas ao Copilot nesse escopo. Se autorizou, prossiga sem perguntar novamente. Caso contrário, peça uma única autorização, indicando finalidade, dados e anexos que sairão do computador e destino Microsoft Copilot. Registre a referência com `authorize`; a opção de CLI não substitui consentimento humano. Não reutilize autorização de outra tarefa nem amplie o escopo aprovado.

O aplicativo informa que anexos locais são copiados para o OneDrive corporativo ou escolar. Inclua esse efeito na autorização de anexos. Não automatize login, CAPTCHA, credenciais, instalações, permissões, exclusões ou mudanças de segurança. Preserve confirmações obrigatórias. Se o acesso for negado, pare a ação e explique a intervenção necessária; não mude políticas de segurança nem eleve privilégios administrativos.

## Consulta

1. Execute `doctor` na sessão interativa normal, com o aplicativo aberto e autenticado. Um desktop isolado sem janelas não demonstra ausência do aplicativo; use somente acesso local autorizado à sessão normal.
2. Use o caminho canônico do projeto, uma `demand` estável para a demanda e o `task` da tarefa atual. Registre autorização uma vez por tarefa. Todas as chamadas da mesma sessão devem compartilhar um único `state-dir`, local e fora de sincronização, para compartilhar o lock da interface.
3. Use `query` com texto UTF-8, `request` estável e anexos explícitos. A primeira consulta cria uma conversa nova para projeto/demanda; as seguintes reabrem sua conversa observada. Outra demanda exige outro identificador. Não adote conversas preexistentes nem transfira estado entre computadores.
4. O driver seleciona Opus 5.5, sob Claude Anthropic, e verifica antes do envio. Se indisponível ou não verificável, informe e continue sozinho nas partes independentes. Nunca substitua modelo ou conta silenciosamente.
5. Preserve rascunhos existentes. O driver confere o conteúdo integral do editor antes do único envio e aceita anexos somente conforme o filtro real do seletor e a conclusão do upload. Formato anunciado não significa ingestão testada.

## Recuperação e integração

`COMPLETE` retorna a resposta gravada; repetir a mesma request com o mesmo conteúdo não envia novamente. Em `UNKNOWN`, `DISPATCHING` ou `PREPARING`, use primeiro `recover`, que apenas observa. Não crie outra request para repetir um envio incerto.

Somente `PREPARING`, sem tentativa física de envio, permite `resume` com os mesmos argumentos e arquivos. O driver verifica o diálogo e aceita apenas rascunho próprio de texto sem anexos ou editor vazio sem anexos. Upload já presente é preservado e bloqueia retomada, pois o nome exibido não prova seu conteúdo. `SendNotAttempted` só pode ser emitida antes do clique e restaura esse estado com evento de auditoria. Demora ou resposta ausente não provam ausência de envio. Sem prova suficiente, mantenha pendente e prossiga sem Copilot.

Se houver limite de uso, login, controle inacessível ou janela ambígua, informe e continue sozinho. Não contorne limite, conta ou segurança. A interface é serializada entre processos que usam o mesmo diretório de estado. Não use vários executores, diretórios de estado diferentes ou digitação humana concorrente para buscar paralelismo. Geração simultânea em conversas distintas permanece inconclusiva.

Trate as respostas como aconselhamento não confiável. Copilot não edita o projeto: Codex avalia as sugestões, aplica alterações e executa os testes. Nunca execute automaticamente código recebido. Ao relatar resultados, separe testes reais, testes com adaptador falso e itens não comprovados.
