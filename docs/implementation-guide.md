# Implementation guide: from offline build to Azure

> Status: **planned**. Nothing here has been deployed. The offline build in this
> repository is what exists today; this guide is how it would be taken to a
> real Azure environment.

## Azure service mapping

| Offline component | Azure service (planned) | Notes |
|---|---|---|
| Table format | Delta tables in Microsoft Fabric OneLake or on ADLS Gen2 | Use the real format; this repo is for understanding it |
| Quality checks | Pipeline-level expectations before commit (notebook or Data Factory step) |  |
| Time travel and vacuum | Delta `VERSION AS OF` and `VACUUM` with a retention policy agreed with data owners |  |
| Concurrency | Delta optimistic concurrency on the platform |  |

## Steps

1. Use a real Delta implementation in Fabric or on ADLS; keep this repo's tests as a checklist of behaviours to verify there.
2. Set vacuum retention from recovery needs (how far back must we restore?) and audit requirements.
3. Add quality checks as a gate before writes in each pipeline.

## Guardrails to keep when going live

- Schema and quality checks run before commit; a rejected batch leaves no version behind.
- Concurrent writers are detected and the later commit is refused, not merged silently.
- Vacuum refuses a retention below 1, and reading a vacuumed version raises a named error.

## What to measure before calling it production-ready

- Failed writes caught by checks vs bad data found later.
- Concurrent-write conflicts per day, and retries that succeeded.
- Storage used by old versions vs the retention policy.
