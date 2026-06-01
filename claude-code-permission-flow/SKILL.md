---
name: claude-code-permission-flow
description: Understand how Claude Code's auto-mode permission classifier works so an agent stops retrying denied commands or routing around denials. Use when a command is auto-denied, the user says "I authorize you / go ahead" in chat, and the agent is tempted to re-run it or rewrite it to dodge the guard. Explains why in-conversation authorization does not count and what actually unblocks.
license: MIT
metadata:
  version: "1.0.0"
  domain: agent-harness
  triggers: permission denied, auto-deny, authorize, classifier, bypass permission, retry denied command
---

# In-conversation authorization does not move the permission classifier

## The trap
Claude Code's auto-mode permission classifier is a **separate enforcement layer that does not read the conversation.** When a command is auto-denied and the user types "I authorize you" / "go ahead" in chat, re-running the same command just gets denied again — the chat approval is invisible to the classifier.

Worse failure mode: "routing around" the denial — rewriting a script so the forbidden verb is hidden, splitting the command, or obfuscating it. That defeats the guard's intent and is the wrong move even when it works.

## The rule
A chat "go ahead" is **not** a permission grant. To actually unblock, the *user* must do one of:
- add a settings permission rule (allowlist the command / pattern),
- run it themselves via the `!` prefix (so its output lands in the session), or
- enable a bypass mode.

The agent's correct response to an auto-deny + chat authorization is to **state which of these the user should do** — not to retry, and not to route around it.

## Why it matters
Retrying burns turns and reads as the agent ignoring the denial. Routing around it silently defeats a control the user put there on purpose. Naming the real unblock path is faster and keeps the guard meaningful.
