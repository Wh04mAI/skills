# Invist-AI Skills

Reusable Claude Code / Claude Agent SDK skills.

## Available Skills

### [`model-fingerprint-interrogator`](./model-fingerprint-interrogator)

Verify whether an LLM gateway's claimed model is genuine. Combines:

- **Smoke signal detection** on raw HTTP probes (gateway middleware exposure, batch-faked timestamps, system-prompt injection)
- **TLS fingerprint spoofing** via `curl_cffi` (Node.js 24.x JA3) to defeat basic anti-bot gates
- **Differential testing** via local `claude` CLI vs Docker-wrapped Claude Code, comparing baseline (real Anthropic) against suspect endpoint
- **Forensic Markdown report** suitable for confronting a gateway operator

Use cases: verifying API gateway claims ("我们用的是真的 Claude Opus 4.7"), pre-purchase due diligence on relay providers, periodic re-audit of upstream vendors.

## Installing a Skill

```bash
# Symlink into your Claude Code skills directory
ln -s "$PWD/<skill-name>" ~/.claude/skills/<skill-name>
```

Or use the Claude Code skills CLI:

```bash
npx skills add Invist-AI/skills@<skill-name>
```
