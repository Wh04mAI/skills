---
name: claude-code-hook-design
description: Design and debug PreToolUse / command-guard hooks for Claude Code (and similar agent harnesses) so they actually enforce without false-positives or bypasses. Use when writing a hook that blocks dangerous commands, when a guard wrongly blocks an innocent command, or when a reviewer keeps finding one more way around a denylist. Covers default-deny vs denylist, per-segment evaluation of compound commands, substring false-positives, and pairing instructions with out-of-band enforcement.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-harness
  triggers: PreToolUse hook, command guard, hook false positive, denylist, default-deny, command parsing, guardrail
---

# Designing command-guard hooks that enforce without misfiring

Four rules learned from building and debugging agent command-guard hooks.

## 1. Pair a loaded instruction with out-of-band enforcement
A guardrail changes behavior only if it is (a) in context the agent loads by default **and** (b) backed by a deterministic gate that fires regardless of whether that context was loaded. A rule buried in a doc the agent reads only when it goes looking does nothing; an instruction-only rule is bypassed the moment the agent acts without loading it. Pair a role-scoped instruction (skill / subagent prompt) with a hook or lint that triggers on the action itself. ("But it's documented" is exactly why documented-only guardrails keep getting violated.)

## 2. Default-deny beats a denylist for open-ended spaces
When the space of "bad" inputs is open-ended (hundreds of modules, arbitrary subcommands), a denylist is whack-a-mole and never converges — the tell is a reviewer who keeps finding "you also missed X." Flip to **default-deny**: allow only a short, explicit read-only allowlist; block everything else. One change closes the whole bypass class, and the allowlist is small and auditable where the denylist was unbounded.

## 3. Evaluate compound commands per-segment
A guard that allows a blessed command and blocks an ad-hoc form is bypassed by `blessed-cmd; dangerous-cmd` — a blessed token anywhere on the line exempts the whole line. Split on shell separators (`;`, `&&`, `||`, `|`, `&`) and test **each segment independently**. Detect the bad invocation by its own token shape, not by excluding blessed names (which chaining neutralizes). Exception: a payload inside a quoted remote arg (`ssh host 'a; b'`) must stay whole-string — splitting severs the verb from its remote context. (In bash, feed the loop via heredoc, not a pipe — piping into `while` runs a subshell and `exit` won't terminate the script.)

## 4. Beware substring false-positives on incidental text
A guard that regexes the whole command string trips on *mentions* of a forbidden verb — in a commit message, an `echo`, a here-doc body, a doc file's prose. When a guard blocks a command that clearly isn't doing the forbidden thing, suspect a substring match. Fixes: split so prose and the real call are separate invocations; reword the prose; or write files via the file tools (Write / Edit), which don't pass through Bash PreToolUse hooks. Silent over-blocking looks like a broken command, not a guard bug — so suspect the guard.

## Why it matters
Hooks are the out-of-band half of every real guardrail. A hook that misfires gets disabled (and takes the protection with it); a hook full of bypasses gives false confidence. Default-deny + per-segment + substring-awareness make it both tight and livable.
