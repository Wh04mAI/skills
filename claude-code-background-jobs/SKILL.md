---
name: claude-code-background-jobs
description: Run long-running commands from a Claude Code agent the right way — via the Bash tool's `run_in_background` option, not a shell `&`. Use when an agent needs to start a multi-minute process (a build, a test run, an external CLI, a server) and keep working or collect its output later. Triggers when a command would otherwise be backgrounded with `&`, `nohup`, or `disown` inside a normal tool call.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-harness
  triggers: background job, long-running command, run_in_background, ampersand, nohup, detach process, BashOutput
---

# Background long-running commands via the harness, not shell `&`

## The trap
Appending `&` (or `nohup … &`, `disown`) to a command inside a normal **foreground** tool call does not reliably detach it. The process is tied to the tool-call boundary: when the call returns, the background process can be orphaned or killed, leaving truncated or empty output. You think a multi-minute job is running; it died the moment the tool call ended, and you act on partial results.

## The rule
For anything long-running, set the Bash tool's **`run_in_background: true`**, not an inline shell `&`. The harness-tracked mechanism:
- keeps the process alive across tool calls (it runs detached, no `&` needed),
- tracks it as a background shell with its own id, and
- notifies you when it completes instead of making you blind-poll.

Reserve inline `&` for genuinely fire-and-forget *local* helpers whose output you will never depend on.

## How to apply
- Starting a build / test / server / external CLI that takes more than a few seconds → `run_in_background: true`.
- Need its output? Wait for the completion notification, then pull it with the `BashOutput` tool against the shell's id — don't `sleep` + re-read a file you piped to.
- Never assume `cmd &` inside one tool call is still running in the next one — the foreground call's process group can be reaped when the call returns.

## Why it matters
The failure is silent: no error, just a job that isn't there and output that's cut off. Using the tracked mechanism turns "is it done yet?" guesswork into an explicit completion signal.
