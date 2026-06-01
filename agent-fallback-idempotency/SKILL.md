---
name: agent-fallback-idempotency
description: Make an agent's fallback / backup timer wakeups idempotent so they don't re-execute already-finished work. Use when a long task arms both a primary completion signal and a backup timer (or any belt-and-suspenders re-trigger). The handler must check state and no-op if the work is already done.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-workflow
  triggers: fallback timer, backup wakeup, idempotent handler, re-trigger, ScheduleWakeup, scheduled wakeup, completion signal, at-least-once
---

# Make fallback-timer wakeups idempotent against finished work

## The trap
When you arm a backup timer alongside a primary completion signal, the timer can fire **after** the work already completed and re-issue the same instruction. A non-idempotent handler then re-runs side-effecting steps — re-deploy, re-merge, duplicate provisioning — on work that already succeeded.

Concretely in Claude Code: you kick off a long background task **and** call `ScheduleWakeup` as insurance against a missed completion notification. The task finishes and notifies you; later the wakeup also fires with the same "now go finish/deploy X" prompt. If that prompt's handler doesn't check first, you do the side-effecting step twice.

## The rule
A fallback handler must **first read the result / state and no-op if the work is already done**, rather than blindly re-executing. Treat the fallback as "run the work *unless it already ran*", never as "run the work again."

## How to apply
- Arming a backup wakeup/timer (e.g. a scheduled wakeup) as insurance against a missed completion notification → make the woken handler check a durable marker before acting.
- Pick a marker that outlives the turn: a result/sentinel file, a status field in state, a deployed-version tag, a git ref, a "done" row. The completion path **writes** it; the fallback path **reads** it and exits early when present.
- Encode the no-op into the wakeup prompt itself — e.g. "if `deploy.done` exists, report already-done and stop; otherwise deploy" — so the check happens before any action even if a fresh session handles the wakeup.
- Applies to any belt-and-suspenders re-trigger, not just timers (retry queues, redelivery, at-least-once messaging).

## Why it matters
Belt-and-suspenders reliability is only safe if the redundant trigger is idempotent; otherwise the safety mechanism itself causes duplicate side effects.
