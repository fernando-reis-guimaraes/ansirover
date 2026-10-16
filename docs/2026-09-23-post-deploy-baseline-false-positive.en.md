# Post-deploy false positive: Swarm baseline captured before the last service update

Date: 2026-09-23
App: `myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4` on CapRover machine `server-a`
Image: `cr.umapps.net/umapps/quantallm:4f009bc2`
Command: `ansirover-restore-upload server-a myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4` (alias `aru`)

## What happened

Three consecutive runs, three different outcomes:

1. `10:14 BRT` — preflight abort, expected:
   `environment variable DB_PASS_VAR referenced by env var template in line 17 ... is not set`.
   Line 17 is `DATABASE_URL=postgresql://...:${DB_PASS_VAR}@...`. The secret was not exported.

2. `10:15 BRT` — database preflight abort, expected:
   `PostgreSQL URI in DATABASE_URL has application_name=mydb but AnsiRover appName is myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4`.
   The inventory was fixed between `10:15` and `10:16`; the same command passed preflight at `10:16:33`.

3. `10:16 BRT` — deploy reached `13/13`, CapRover reported
   `Build has finished successfully!`, metadata and env verification passed
   (`metadata persisted OK`, `inventory environment variables match live state OK`),
   then AnsiRover failed the run:
   `post-deploy task churn detected: new Failed/Rejected task appeared before convergence for service: srv-captain--myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4`.

   Task list at failure:
   `Ready 1s ago` (new, healthy), `Failed 2s ago: No such container: srv-captain--...`,
   plus older `Shutdown` entries from the rolling update.

## Application evidence: healthy

Container logs (`13:33-13:41 UTC`) show a healthy boot, not a crash:

- `Generated Prisma Client (v5.3.1)`, `10 migrations ... All migrations have been successfully applied`
- `Primary server in HTTP mode listening on port 3001`
- `[MCPHypervisor] Successfully started 2 MCP servers`
- `sent_chat` / `workspace_thread_created` served
- `Child process exited with code 0` lines are background cleanup jobs finishing normally
- `SSE stream disconnected: terminated` is the remote MCP endpoint closing a stream, not the container dying

No non-zero exit, no `OOMKilled`, no Node stack trace.

## Root cause

`restore_one_app` captured `baseline_task_ids = get_task_state_snapshot(...)` **before**
`restore_one_app_steps`. The restore then performs two service updates around the deploy:

- `5/13` apply config with `forceSsl=false`
- `9/13` deploy image
- `11/13` reapply config with `forceSsl=true`

Each update creates a new Swarm task generation. The intermediate generation briefly produced
`Failed: No such container` — a known Swarm/Docker removal race, not an application error.
Because the baseline predates the restore's own churn, `wait_for_swarm_convergence` classified
that intermediate task as a "new" failure and aborted while `running < desired` during the handover.

The check itself started after `13/13`, but its baseline did not: it was anchored before the
first churn instead of after the last one.

## Why "wait for Ready first" was rejected

Requiring `Running == desired` before starting observation would mask the exact failures the
guardrail must catch: infinite crash loops and database-connection failures where the service
never reaches the desired replica count. The convergence failure path (`running < desired` +
new `Failed/Rejected`) must stay. What must change is only the anchor.

## Mitigation plan (implemented)

1. Re-baseline Swarm task IDs **after** `restore_one_app_steps` and after
   `verify_ansirover_metadata` / `verify_live_env_drift`, immediately before
   `wait_for_swarm_convergence`. Intermediate tasks from the `forceSsl=false` apply and the
   image deploy are then part of the baseline and no longer count as new failures.
2. Keep the failure semantics unchanged after the new anchor: any `Failed/Rejected` task ID
   not in the post-restore baseline still fails the run while `running < desired`; the
   stability window (`swarmStabilityPeriod`, default `15s`) and the PostgreSQL reconnect
   monitor still run afterward, so crash loops and reconnect loops are still quarantined.
3. Optional follow-up (not implemented here): skip the double apply when base-domain SSL is
   already enabled, halving self-inflicted churn; or anchor on `docker service inspect
   UpdatedAt` / task `CreatedAt` instead of a second snapshot.

## Verification

- `python3 -m unittest discover -s tests` passes, including new regression coverage asserting
  the baseline snapshot happens after the restore steps and verification, not before.
- Redeploy of `myapp-a1b2c3d4e5f6a7b8c9d0e1f2a3b4` on `server-a` with the fixed tool.
