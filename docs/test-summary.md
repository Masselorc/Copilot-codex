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

A instalação pessoal foi validada posteriormente pelo catálogo do runtime Codex com recarga: skill em escopo de usuário, habilitada e sem erros de descoberta. O cliente instalado também concluiu uma consulta textual curta e sintética com Opus 5.5; foi conferido um único turno e a repetição em cache sem acesso ao adaptador. Estado, IDs e resposta desse teste permanecem locais.

A atualização dos gatilhos amplia os critérios de oito para 14 e altera somente instruções e documentação. Ela não muda o transporte Python nem adiciona envios automáticos. A validação estrutural da skill e os hashes do pacote e da cópia instalada foram conferidos após a atualização; não foi necessária nova consulta ao Copilot para essa mudança de texto.

- Modelo e controles dependem da interface em português observada. Indisponibilidade ou ambiguidade bloqueiam o envio.
- TXT e PNG têm teste real; demais formatos dependem do filtro exibido e ainda precisam de teste de ingestão.
- Hashes e chips verificam somente estado local e nomes visíveis, não os bytes no servidor. Não edite anexos durante a operação. Uploads já presentes bloqueiam `resume`; alteração detectada durante upload impede o pedido, mas a cópia no OneDrive pode já ter ocorrido.
- Respostas longas/virtualizadas, tabelas e citações podem exigir revisão; U+FFFC pode permanecer no texto. A leitura completa refere-se ao intervalo que a interface expõe.
- O lock só coordena clientes que compartilham o mesmo diretório de estado. Não controla digitação humana ou uso direto dos módulos.
- Não existe garantia de entrega exatamente uma vez. Após tentativa incerta, o cliente preserva o estado e exige recuperação, sem repetir envio.
- Paralelismo remoto estrito não foi comprovado; o CLI mantém um único executor da interface.
- Autorizações e estado são locais e não devem ser migrados com a skill.
