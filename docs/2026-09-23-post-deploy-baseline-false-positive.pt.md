# Falso positivo no pós-deploy: baseline do Swarm capturada antes do último update

> Nota: versão em português fornecida por pedido explícito do usuário. A versão em inglês
> (`2026-09-23-post-deploy-baseline-false-positive.en.md`) é a canônica conforme o `AGENTS.md`.

Data: 23/09/2026
App: `myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4` no CapRover `srv1`
Imagem: `cr.umapps.net/umapps/quantallm:4f009bc2`
Comando: `ansirover-restore-upload srv1 myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4` (alias `aru`)

## O que aconteceu

Três execuções seguidas, três resultados diferentes:

1. `10:14 BRT` — aborto de preflight, esperado:
   `environment variable DB_PASS_VAR ... is not set`.
   A linha 17 é o `DATABASE_URL` com `${DB_PASS_VAR}`. O segredo não tinha sido exportado.

2. `10:15 BRT` — aborto no preflight de banco, esperado:
   `... has application_name=mydb but AnsiRover appName is myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4`.
   O inventário foi corrigido entre `10:15` e `10:16`; o mesmo comando passou às `10:16:33`.

3. `10:16 BRT` — deploy foi até `13/13`, o CapRover informou
   `Build has finished successfully!`, metadados e env validados
   (`metadata persisted OK`, `env vars match live state OK`),
   e então o AnsiRover reprovou a execução:
   `post-deploy task churn detected: new Failed/Rejected task appeared before convergence`.

   Lista de tasks na falha:
   `Ready 1s` (nova, saudável), `Failed 2s: No such container: srv-captain--...`,
   mais `Shutdown` antigos do rolling update.

## Evidência: aplicativo saudável

Os logs do container (`13:33-13:41 UTC`) mostram boot saudável, não crash:

- `Generated Prisma Client`, `10 migrations ... successfully applied`
- `Primary server in HTTP mode listening on port 3001`
- `[MCPHypervisor] Successfully started 2 MCP servers`
- `sent_chat` / `workspace_thread_created` atendidos
- `Child process exited with code 0` são jobs de limpeza terminando normalmente
- `SSE stream disconnected` é o remoto MCP fechando stream, não queda do container

Sem exit não-zero, sem `OOMKilled`, sem stack trace Node.

## Causa raiz

O `restore_one_app` capturava `baseline_task_ids = get_task_state_snapshot(...)` **antes**
do `restore_one_app_steps`. O restore faz dois updates de serviço em torno do deploy:

- `5/13` aplica config com `forceSsl=false`
- `9/13` deploy da imagem
- `11/13` reaplica config com `forceSsl=true`

Cada update cria uma nova geração de tasks no Swarm. A geração intermediária produziu
`Failed: No such container` — corrida conhecida Swarm/Docker na remoção, não erro do app.
Como a baseline era anterior ao churn do próprio restore, o `wait_for_swarm_convergence`
classificou essa task intermediária como falha "nova" e abortou com `running < desired`
durante a troca.

O check começava depois do `13/13`, mas a baseline não: ela estava ancorada antes do
primeiro churn, não depois do último.

## Por que "esperar Ready primeiro" foi recusado

Exigir `Running == desired` antes de observar mascararia exatamente as falhas que o
guardrail precisa pegar: loop infinito de crash e falha de conexão com banco, onde o
serviço nunca atinge as réplicas desejadas. O caminho de falha (`running < desired` +
`Failed/Rejected` novo) precisa continuar existindo. Só a âncora precisa mudar.

## Plano de mitigação (implementado)

1. Recapturar os IDs de task do Swarm **depois** do `restore_one_app_steps` e depois do
   `verify_ansirover_metadata` / `verify_live_env_drift`, imediatamente antes do
   `wait_for_swarm_convergence`. As tasks intermediárias do `forceSsl=false` e do deploy
   passam a fazer parte da baseline e não contam mais como falhas novas.
2. Manter a semântica de falha após a nova âncora: qualquer task `Failed/Rejected` fora da
   baseline pós-restore ainda reprova enquanto `running < desired`; a janela de
   estabilidade (`swarmStabilityPeriod`, padrão `15s`) e o monitor de reconnect PostgreSQL
   continuam depois, então crash loops e reconnect loops seguem sendo isolados.
3. Acompanhamento opcional (não implementado aqui): pular o duplo apply quando o SSL do
   domínio-base já está ativo, reduzindo o churn autoinfligido pela metade; ou ancorar em
   `docker service inspect UpdatedAt` / `CreatedAt` das tasks em vez de um segundo snapshot.

## Verificação

- `python3 -m unittest discover -s tests` passa, incluindo regressão nova que afirma que o
  snapshot de baseline acontece depois dos steps do restore e da verificação, não antes.
- Redeploy do `myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4` no `srv1` com a ferramenta corrigida.
