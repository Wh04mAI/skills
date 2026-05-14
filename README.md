# Invist-AI Skills

Central repository for Invist-AI's shared Claude Code / Claude Agent SDK skills.

## What's in here

The repo hosts two groups of skills:

### Standalone skills

- **[`model-fingerprint-interrogator`](./model-fingerprint-interrogator)** — Verify whether an LLM gateway's claimed model is genuine. Combines smoke-signal detection on raw HTTP probes, TLS fingerprint spoofing via `curl_cffi`, differential testing against a baseline Anthropic endpoint, and a forensic Markdown report. Use for pre-purchase due-diligence on relay providers or periodic re-audit of upstream vendors.

### BMAD-Team — multi-agent SDLC extension

A coherent set of skills that extends the [BMAD methodology](https://github.com/bmad-code-org/BMAD-METHOD)
to **multi-agent, multi-environment software delivery**. BMAD core
provides a single-threaded agentic SDLC; BMAD-Team adds the three
things BMAD does not cover natively: parallel multi-agent coordination,
cross-model pre-merge review, and remote-environment delivery.

```
bmad-team/                 # Charter + shared protocol spec (NOT a skill)
  charter.md               # What BMAD-Team is and why it exists
  protocol.md              # ORCHESTRATE:* marker spec (novel piece)

bmad-team-claim/           # Skill: claim a story via draft PR
bmad-team-orchestrate/     # Skill: dispatch & monitor parallel agents
bmad-team-review/          # Skill: cross-model pre-merge review
bmad-team-deploy/          # Skill: ship branch to dev / staging k3s env
                           # (bmad-team-verify is deferred — see charter Roadmap)

_bmad/team/config.yaml.example   # Unified per-project config template
```

Start with [`bmad-team/charter.md`](./bmad-team/charter.md) to understand the model.

## Installing a skill

### Standalone (single skill)

```bash
# Symlink into your Claude Code skills directory
ln -s "$PWD/<skill-name>" ~/.claude/skills/<skill-name>
```

Or via the skills CLI:

```bash
npx skills add Invist-AI/skills@<skill-name>
```

### BMAD-Team (whole bundle)

Add this repo as a git submodule at `.claude/skills` in your project:

```bash
git submodule add git@github.com:Invist-AI/skills.git .claude/skills
git submodule update --init --recursive

# Later, pull latest:
git submodule update --remote --merge
```

Copy `_bmad/team/config.yaml.example` to `{your-project}/_bmad/team/config.yaml` and edit for your environment.

## Status

- `model-fingerprint-interrogator`: standalone, production-ready.
- `bmad-team-*`: v0 internal. The protocol spec at `bmad-team/protocol.md`
  is the candidate for eventual external publication (working title:
  `pr-claim-protocol`).

## License

MIT.
