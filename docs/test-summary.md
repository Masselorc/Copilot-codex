# Testes e limitações

## Testes reais anteriores à distribuição

Foram realizados na interface autenticada de Microsoft 365 Copilot em português, com Python 3.14.7 de 64 bits, usando exclusivamente texto e arquivos sintéticos autorizados. Os registros completos permanecem locais, pois contêm IDs de conversas e informações operacionais; não são fixtures deste repositório.

| Caso | Resultado observado |
| --- | --- |
| Texto mínimo | Envio e recuperação da resposta concluídos. |
| Modelo | Opus 5.5 selecionado e indicação de uso observada; novo chat redefinia o seletor para Automático antes da seleção. |
| TXT sintético | Informação presente apenas no arquivo e soma esperada recuperadas. |
| PNG sintético | Contagem de três retângulos recuperada após tornar visível o controle Enviar. Uma tentativa anterior permaneceu incerta e não foi reenviada. |
| Código | Indentação recuperada; função aplicada e testada localmente por Codex. |
| Continuidade e isolamento | Conversas distintas recuperadas por seus próprios IDs, sem cruzar marcadores de contexto observados. |
| Idempotência | Repetição de request concluída retornou resultado local sem novo envio. |
| Recuperação | Resposta de request inicialmente incerta recuperada sem reenvio. Falha comprovadamente anterior ao clique retomada com rascunho próprio e evento de auditoria. |
| Alternância durante geração | Resultados finais recuperados em duas conversas isoladas; geração estritamente simultânea inconclusiva por invalidação transitória da árvore UIA. Segunda abertura reutilizou a janela. |

Essas observações descrevem o ambiente testado, não garantem o comportamento de outra conta, máquina ou versão. Não houve instalação de extensão, extração de credenciais, mudança de política ou elevação administrativa. O cliente final não executa PowerShell.

## Testes locais reproduzíveis

```powershell
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

`test_protocol.py` usa adaptador falso e SQLite local: autorização e escopo, vinculação de conversa, conflitos de request, limites, idempotência, recuperação, falhas anteriores e posteriores ao envio e lock entre processos. `test_response_text.py` verifica extração sintética de código e rejeição de estrutura incompleta. Esses 25 testes não abrem Copilot nem comprovam comportamento da interface real.

`test_installation.py` testa o instalador em diretórios temporários, com cópia por allowlist, preservação de destino existente, rejeição de fonte insegura/incompleta e `--help` do cliente instalado, sem instalação global ou acesso à interface. Os testes de instalação também não comprovam acesso ao modelo em outro computador.

A validação estrutural da skill passou. A primeira tentativa no sandbox foi bloqueada ao criar filhos de diretórios temporários; a suíte foi executada com acesso local autorizado ao workspace, sem acesso à interface ou mudança de segurança. O teste com symlink real foi ignorado porque a conta Windows não permitia criá-lo; sua recusa existe no instalador, mas esse caso real não foi comprovado.

A revisão anterior à publicação acrescentou reconferência de hashes e chips de anexos, recusa de nomes duplicados e de retomada com uploads preexistentes, e checagem do posicionamento do cursor antes do clique. As novas guardas têm testes sintéticos com mocks e arquivos artificiais; não foram exercitadas em uma nova consulta real. Os testes reais da tabela ocorreram antes desses reforços. `test_app_attachments.py` e `test_native_click.py` não abrem a interface nem enviam conteúdo.

A suíte final executou 47 testes: 46 aprovados e um ignorado (symlink real sem permissão). O pacote ZIP foi verificado por allowlist e hashes e a suíte foi repetida em sua cópia isolada, sem instalação global.

## Limites conhecidos

### Correção após timeouts nativos

Uma captura fornecida pelo usuário registrou 22 execuções, zero bloqueios e três falhas por timeout de dois segundos: duas de authorize e uma de cost. Também mostrou conclusão de SessionStart/session, PostToolUse/failures e outra ocorrência de cost. Isso é evidência de eventos nativos anteriores à correção; não comprova as cinco regras nem a eficácia da guarda de autorização. A leitura da Library confirmou o texto e metadados; a materialização local pelo helper oficial foi impedida por ausência de os.setxattr no Windows, sem contorno desse fluxo.

Medições locais com entradas sintéticas separaram inicialização, execução do handler e shell sem perfil. Na primeira amostra, Python direto ficou em cerca de 22–35 ms e handlers em até 357 ms em rajadas de seis processos. O shell com handler chegou a 1.697 ms em rajadas de quatro. Sob carga adicional, até a inicialização Python variou para cerca de um segundo e um handler chegou a 1.769 ms. Não se reproduziram os três timeouts nem se isolou sua causa exata. As amostras ocorreram sob cargas diferentes; não são prova de ganho percentual da correção.

Foi identificado e removido trabalho desnecessário: abertura/inicialização de schema e transação de escrita para chamadas alheias ao envio. authorize agora ignora chamadas irrelevantes antes de qualquer acesso ao estado, e consulta autorização/estado somente em leitura. cost sem correspondência não obtém trava de escrita. Atualizações de avisos e contadores continuam atômicas. O timeout externo passou a 10 segundos para acomodar lançamento do shell/processo e variação observada; SQLite mantém limite de 100 ms. Não foi retirado o controle de autorização, criado processo persistente ou alterada segurança.

A suíte atual executou 82 testes: 81 aprovados e um ignorado (symlink Windows). Além dos casos anteriores, cobre ausência de acesso ao estado em autorização irrelevante, leitura sem transação de escrita, banco exclusivamente bloqueado e recusa do CLI antes da UI quando a autorização está ausente ou o ledger está bloqueado. São testes sintéticos. O runtime pode continuar após falha/timeout de hook; a recusa explícita do handler e a checagem independente do CLI/protocolo são distintas. Não se presume fail-closed do runtime.

A mudança de timeout exige nova revisão de confiança das cinco definições. A versão corrigida ainda precisa de confirmação de disparos e efeitos na conversa nativa após essa revisão. A tarefa delegada com ferramentas locais não oferece prova equivalente: a documentação não suporta hooks de comando locais em orquestração cloud. Sem nova consulta Copilot nesta correção. Consulte [o guia](../skill/copilot-consult/references/hooks.md).

A instalação pessoal foi validada posteriormente pelo catálogo do runtime Codex com recarga: skill em escopo de usuário, habilitada e sem erros de descoberta. O cliente instalado também concluiu uma consulta textual curta e sintética com Opus 5.5; foi conferido um único turno e a repetição em cache sem acesso ao adaptador. Estado, IDs e resposta desse teste permanecem locais.

A atualização intermediária ampliou os critérios de oito para 14 somente nas instruções. A etapa seguinte adicionou cinco critérios (total 19), cinco regras de hooks opcionais, atualização gerenciada com backup, revogação de autorização e identificação de escopo no cliente. Não foram feitas novas consultas reais ao Copilot nesta etapa de hooks; os testes do envio atualizado usam adaptadores falsos.

A suíte de hooks executou 78 testes: 77 aprovados e um ignorado (symlink real sem permissão). Os novos testes cobrem as cinco regras positivas/negativas, payload desconhecido/inválido, cancelamento e permissão negada, ausência/revogação/divergência de autorização, repetição, retomada, mudança de etapa/tarefa, limites de estado, remoção preservando outras configurações, rollback se a gravação do recibo falhar, atualização de instalação antiga e pacote em outro caminho. Os eventos desses testes são simulados, inclusive quando o handler é executado como processo Python.

Os schemas foram gerados pelo Codex Windows instalado, versão 0.162.0-alpha.17.2. A verificação via hooks/list reconheceu cinco definições em perfil temporário isolado e, depois do registro autorizado, cinco no perfil pessoal real: todas não confiáveis, sem erros. A skill atualizada também foi reconhecida em escopo de usuário. Essas leituras não iniciaram conversa nem turno de modelo. Descoberta de definição não significa execução real do evento nem comprovação da exibição visual na janela aberta. A confiança exige revisão do usuário em /hooks; não foi aprovada automaticamente. Consulte [o guia](../skill/copilot-consult/references/hooks.md) antes de ativar em outra máquina.

Depois da ação de confiança realizada pessoalmente pelo usuário, nova leitura confirmou as cinco definições confiáveis e habilitadas. Comandos, eventos, matchers e timeout correspondiam às definições finais, e os 11 arquivos instalados conferiam em bytes e hashes com o pacote. Nenhuma confiança foi alterada pelo verificador. Os handlers instalados passaram em 16 verificações adicionais com JSON sintético e estado temporário isolado, sem criar autorização ou abrir Copilot. Uma sessão efêmera local, sem turno de modelo, não apresentou notificações de execução de SessionStart; portanto a execução nativa desse evento permanece sem comprovação. Os demais eventos também não foram exercitados por um turno real. Se a definição mudar posteriormente, o usuário deverá revisar a nova confiança.

- Modelo e controles dependem da interface em português observada. Indisponibilidade ou ambiguidade bloqueiam o envio.
- TXT e PNG têm teste real; demais formatos dependem do filtro exibido e ainda precisam de teste de ingestão.
- Hashes e chips verificam somente estado local e nomes visíveis, não os bytes no servidor. Não edite anexos durante a operação. Uploads já presentes bloqueiam `resume`; alteração detectada durante upload impede o pedido, mas a cópia no OneDrive pode já ter ocorrido.
- Respostas longas/virtualizadas, tabelas e citações podem exigir revisão; U+FFFC pode permanecer no texto. A leitura completa refere-se ao intervalo que a interface expõe.
- O lock só coordena clientes que compartilham o mesmo diretório de estado. Não controla digitação humana ou uso direto dos módulos.
- Não existe garantia de entrega exatamente uma vez. Após tentativa incerta, o cliente preserva o estado e exige recuperação, sem repetir envio.
- Paralelismo remoto estrito não foi comprovado; o CLI mantém um único executor da interface.
- Autorizações e estado são locais e não devem ser migrados com a skill.
