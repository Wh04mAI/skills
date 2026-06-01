---
name: claude-code-background-jobs
description: Run long-running commands from a Claude Code agent the right way — via the harness's first-class background mechanism, not a shell `&`. Use when an agent needs to start a multi-minute process (a build, a test run, an external CLI, a server) and keep working or collect its output later. Triggers when a command would otherwise be backgrounded with `&`, `nohup`, or `disown` inside a normal tool call.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-harness
  triggers: background job, long-running command, run in background, ampersand, nohup, detach process
---

# Background long-running commands via the harness, not shell `&`

## The trap
Appending `&` (or `nohup … &`, `disown`) to a command inside a normal **foreground** tool call does not reliably detach it. The process is tied to the tool-call boundary: when the call returns, the background process can be orphaned or killed, leaving truncated or empty output. You think a multi-minute job is running; it died the moment the tool call ended, and you act on partial results.

## The rule
For anything long-running, use the harness's **first-class background mechanism** (the tool's "run in background" option / dedicated background-run flag), not an inline shell `&`. The proper mechanism:
- keeps the process alive across tool calls,
- tracks it, and
- gives you a completion signal instead of blind polling.

Reserve inline `&` for genuinely fire-and-forget *local* helpers whose output you will never depend on.

## How to apply
- Starting a build / test / server / external CLI that takes more than a few seconds → background-run option.
- Need its output? Let the harness notify on completion, then read the captured output — don't `sleep` + re-read a file you piped to.
- Never assume `cmd &` inside one tool call is still running in the next one.

## Why it matters
The failure is silent: no error, just a job that isn't there and output that's cut off. Using the tracked mechanism turns "is it done yet?" guesswork into an explicit completion signal.
