# Wh04mAI Skills

Central repository for Wh04mAI's shared Claude Code / Claude Agent SDK skills.

## What's in here

The repo hosts two groups of skills:

### Standalone skills

- **[`model-fingerprint-interrogator`](./model-fingerprint-interrogator)** — Verify whether an LLM gateway's claimed model is genuine. Combines smoke-signal detection on raw HTTP probes, TLS fingerprint spoofing via `curl_cffi`, differential testing against a baseline Anthropic endpoint, and a forensic Markdown report. Use for pre-purchase due-diligence on relay providers or periodic re-audit of upstream vendors.
- **[`priority-tags`](./priority-tags)** — Manage MoSCoW + incident priority labels on a GitHub repo. Bootstrap the label set, apply `priority/now|must|should|could|wont` to issues/PRs with mutual exclusion, run a triage pass, and produce a health report (stale incidents, must-inflation). Built for small teams that want one consistent priority scheme across all their repos.

### BMAD-Team — multi-agent SDLC extension

A coherent set of skills that extends the [BMAD methodology](https://github.com/bmad-code-org/BMAD-METHOD)
to **multi-agent, multi-environment software delivery**. Since
2026-08 the positioning is **official-first**: the inner implement
loop belongs to BMAD's official
[bmad-loop](https://github.com/bmad-code-org/bmad-loop) module, and
BMAD-Team supplies only what upstream does not have — the **GitHub
coordination layer**: draft-PR claims via the `ORCHESTRATE:*` marker
protocol, path-routed cross-model review, hygienic squash-merge, and
remote-environment delivery. See the charter's "Direction
(2026-08-02)" section for the reasoning.

```
bmad-team/                 # Charter + shared protocol spec (NOT a skill)
  charter.md               # What BMAD-Team is and why it exists
  protocol.md              # ORCHESTRATE:* marker spec (novel piece)

bmad-team-claim/           # Skill: claim a story via worktree + draft PR (core asset)
bmad-team-dev/             # Skill: PR-flow shell; inner loop delegates to official bmad-loop
bmad-team-review/          # Skill: cross-model review routing + stop rules (called by -dev)
bmad-team-merge/           # Skill: squash-merge + clean up worktree/branches/issue (core asset)
bmad-team-orchestrate/     # Skill: marker monitor (DECISION/APPROVAL); dispatch role is legacy
bmad-team-deploy/          # Skill: ship branch to dev / staging k3s env (project CD glue)
                           # (bmad-team-verify is deferred — see charter Roadmap)

_bmad/team/config.yaml.example   # Unified per-project config template
```

The single-agent pipeline runs: `bmad-team-claim` → [HUMAN CONFIRM] →
`bmad-team-dev` (inner loop wraps `bmad-team-review`) → [HUMAN CONFIRM] →
`bmad-team-merge`. `bmad-team-orchestrate` runs multiple such pipelines
in parallel; `bmad-team-deploy` runs after merge for environments that
need it.

Start with [`bmad-team/charter.md`](./bmad-team/charter.md) to understand the model.

## Installing a skill

### Standalone (single skill)

```bash
# Symlink into your Claude Code skills directory
ln -s "$PWD/<skill-name>" ~/.claude/skills/<skill-name>
```

Or via the skills CLI:

```bash
npx skills add Wh04mAI/skills@<skill-name>
```

### BMAD-Team (whole bundle)

Add this repo as a git submodule at `.claude/skills` in your project:

```bash
git submodule add git@github.com:Wh04mAI/skills.git .claude/skills
git submodule update --init --recursive

# Later, pull latest:
git submodule update --remote --merge
```

Copy `_bmad/team/config.yaml.example` to `{your-project}/_bmad/team/config.yaml` and edit for your environment.

## Status

- `model-fingerprint-interrogator`: standalone, production-ready.
- `bmad-team-*`: v0.1 internal, official-first alignment landed 2026-08-02
  (inner loop delegated to official bmad-loop; this repo keeps the GitHub
  coordination layer). The protocol spec at `bmad-team/protocol.md` is the
  candidate for eventual external publication (working title:
  `pr-claim-protocol`).

## License

MIT.
