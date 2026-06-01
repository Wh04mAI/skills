---
name: agent-fallback-idempotency
description: Make an agent's fallback / backup timer wakeups idempotent so they don't re-execute already-finished work. Use when a long task arms both a primary completion signal and a backup timer (or any belt-and-suspenders re-trigger). The handler must check state and no-op if the work is already done.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-workflow
  triggers: fallback timer, backup wakeup, idempotent handler, re-trigger, scheduled wakeup, completion signal
---

# Make fallback-timer wakeups idempotent against finished work

## The trap
When you arm a backup timer alongside a primary completion signal, the timer can fire **after** the work already completed and re-issue the same instruction. A non-idempotent handler then re-runs side-effecting steps — re-deploy, re-merge, duplicate provisioning — on work that already succeeded.

## The rule
A fallback handler must **first read the result / state and no-op if the work is already done**, rather than blindly re-executing. Treat the fallback as "run the work *unless it already ran*", never as "run the work again."

## How to apply
- Arming a backup timer as insurance against a missed completion notification → make its handler check a durable marker (a result file, a status field, a sentinel) before acting.
- The completion path writes that marker; the fallback path reads it and exits early when present.
- Applies to any belt-and-suspenders re-trigger, not just timers (retry queues, redelivery, at-least-once messaging).

## Why it matters
Belt-and-suspenders reliability is only safe if the redundant trigger is idempotent; otherwise the safety mechanism itself causes duplicate side effects.
