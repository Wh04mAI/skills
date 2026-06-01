---
name: agent-review-loop-discipline
description: Run an iterative AI review→fix loop (including cross-model review via an external CLI) without fabricating convergence or stopping at the wrong time. Use when an agent drives multiple rounds of automated code review and must decide when it is actually done. Covers separating "read the findings" from "declare converged", the repeats-vs-new-findings stopping rule, and confirming an external review CLI actually finished.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-workflow
  triggers: code review loop, cross-model review, iterative review, converged, review fix loop, all clear, external review CLI
---

# Discipline for agent-driven iterative review loops

## 1. Reading the output and declaring "converged" are SEPARATE steps
Agents are prone to emitting a confident "all clear, converged" summary **before** actually parsing the reviewer's output — a fabricated convergence that ships unreviewed findings. Force the count+verdict to be a discrete *prior* step: mechanically pull the finding count (grep the severity markers) and read the verdict text first; only then may you claim convergence or proceed to merge. Never batch "read review" and "declare done" into one action.

## 2. Treat each new finding as real; a fix can introduce a new defect
"Another finding appeared in round N+1" is **normal convergence**, not a reason to hand-wave — your own previous fix can introduce a fresh defect that a later round catches. Treat each new finding as real unless it literally repeats an unaddressed prior one.

## 3. Cap on repeated disputes, not on new findings
A round-cap exists to stop subjective nit-loops, not convergent correctness work. Distinguish: (a) the reviewer **repeats** a finding you already addressed or disputed → stop and escalate to a human, more rounds won't help; (b) the reviewer surfaces a genuinely **new, concrete, verifiable** bug each round → keep going until the structural fix lands. Judge by repeating-vs-advancing, not by the round counter. (Empirically, substantive changes can run many rounds — don't pre-budget 1–2 and bail when real findings keep coming.)

## 4. An external review CLI: output present ≠ run finished
A long external review CLI dumps its internal tool-use traces *and* the final review to stdout. A non-zero exit (e.g. SIGTERM) or a truncated transcript means **interrupted**, not clean. Never treat "the output file has content" as "the run completed" — grep for the explicit terminal verdict / summary marker the tool emits at the end; if it's absent, re-run rather than reporting a result the tool never produced. Don't parse the file until the process has actually exited.

## Why it matters
The expensive failure is a fabricated "passed" — shipping unreviewed code wearing a green checkmark. These rules make convergence something you *verify*, not something you *announce*.
