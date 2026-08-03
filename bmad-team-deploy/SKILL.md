---
name: bmad-team-deploy
description: Deploy the current branch to a target k3s environment (dev / staging / etc.) via jump host using kustomize. Reads env-specific config (host, repo path, namespace, deployments, overlay, smoke URL) from the project's _bmad/team/config.yaml. Use when the user says "deploy to dev", "deploy to staging", "ship this to <env>".
license: MIT
metadata:
  version: "1.0.0"
  domain: workflow
  triggers: deploy, ship, deploy to dev, deploy to staging, kustomize apply, rollout
  role: workflow
  scope: delivery
  part-of: bmad-team
---

# bmad-team-deploy

> Part of [BMAD-Team](../bmad-team/charter.md). Runs **after** a PR merges to the integration branch — the "ship the story" step that takes BMAD past `done`.

Deploys the current branch to a k3s environment using kustomize.
Generic — all environment-specific values come from
`_bmad/team/config.yaml` in the consuming project. The skill itself
hard-codes nothing.

## Usage

```
/bmad-team-deploy <env>
```

Where `<env>` is a key under `deploy.envs.*` in the project's
`_bmad/team/config.yaml`. Common values: `dev`, `staging`.

## Required project config

`_bmad/team/config.yaml` must define:

```yaml
deploy:
  envs:
    dev:
      jump_host: jump.example.com     # ssh user@host
      jump_user: ops
      remote_repo: /opt/myapp         # path on jump host
      kubeconfig: /home/ops/.kube/config
      overlay: infra/k8s/overlays/dev # path inside remote_repo
      namespace: myapp-dev
      deployments:                    # what to rollout-restart (required, must be non-empty)
        - myapp-api
        - myapp-web
      smoke_url: https://dev.myapp.example.com/healthz
      timeout: 120s
      notes: |                        # surfaced in output after deploy
        Example: dev does not deploy the reporting CronJob; an empty reports list is normal.

    staging:
      jump_host: jump.example.com
      jump_user: ops
      remote_repo: /opt/myapp
      kubeconfig: /home/ops/.kube/config
      overlay: infra/k8s/overlays/staging
      namespace: myapp-staging
      deployments:
        - myapp-api
        - myapp-web
      smoke_url: https://staging.myapp.example.com/healthz
      timeout: 120s
      notes: |
        Staging mirrors production config; treat deploys with care.
```

## Prerequisites

- SSH access to `jump_host` as `jump_user` (key already configured).
- Repo checked out at `remote_repo` on the jump host.
- `kubectl` available on the jump host, pointed at `kubeconfig`.

## Steps

The skill expands `${VARS}` from the resolved env block of
`_bmad/team/config.yaml` and runs:

### 1. Determine current branch locally

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
```

### 2. Push branch to origin (idempotent)

```bash
git push origin "$BRANCH"
```

### 3. Update repo on jump host and checkout the branch

```bash
ssh -o StrictHostKeyChecking=accept-new ${jump_user}@${jump_host} \
  "cd ${remote_repo} && git fetch origin && git checkout ${BRANCH} && git pull origin ${BRANCH}"
```

### 4. Apply the kustomize overlay

```bash
ssh ${jump_user}@${jump_host} \
  "cd ${remote_repo} && KUBECONFIG=${kubeconfig} kubectl apply -k ${overlay}"
```

### 5. Restart deployments (force re-pull of rolling branch tags)

**Pre-condition**: `${deployments}` must be a non-empty list. With an empty list the command below expands to `kubectl rollout restart -n <ns>` and exits with `required resource not specified`. If your env genuinely has nothing to rollout-restart (rare), define `pre_rollout_only: true` in the env block and skip this step — but the current schema requires at least one deployment.

```bash
ssh ${jump_user}@${jump_host} \
  "KUBECONFIG=${kubeconfig} kubectl rollout restart ${deployments[@]/#/deploy/} -n ${namespace}"
```

### 6. Wait for rollout

```bash
ssh ${jump_user}@${jump_host} \
  "KUBECONFIG=${kubeconfig} kubectl rollout status ${deployments[@]/#/deploy/} -n ${namespace} --timeout=${timeout}"
```

### 7. Verify pod health

```bash
ssh ${jump_user}@${jump_host} \
  "KUBECONFIG=${kubeconfig} kubectl get pods -n ${namespace}"
```

### 8. Smoke check

```bash
ssh ${jump_user}@${jump_host} \
  "curl -s -o /dev/null -w '%{http_code}' ${smoke_url}"
```

Pass = HTTP `200`. Anything else → report the code, do not declare success.

### 9. Print env-specific notes

If `deploy.envs.<env>.notes` is set, print it after the smoke check
so the operator sees env-specific gotchas (e.g. "dev does not deploy
pipelines CronJob").

## Output

A compact status block:

```
deploy:<env>
  branch: <BRANCH>
  rollout: ok (durations per deployment)
  smoke:   200
  notes:   <env notes, if any>
```

## Notes

- Images are expected to use rolling branch tags (`dev`, `staging`,
  …) so `imagePullPolicy: Always` + `rollout restart` pulls the
  latest. If your project uses immutable tags, the skill should be
  invoked after a build step that produces a new tag, and the
  overlay should reference the new tag.
- API migrations: if the API auto-runs migrations on startup, no
  explicit migration step is needed; the rollout-restart triggers it.
  If your project requires a separate migration job, run it between
  step 4 and step 5 — file a v0.1 enhancement to add a `pre_rollout`
  hook list to the env config.

## Anti-patterns

- ❌ Hard-coding a real jump host or repo path in this skill. Every host-specific value comes from project config.
- ❌ Deploying without a PR being merged to base. This skill ships **the current branch**; if the integration branch has more commits, deploy from `base`, not from your feature branch.
- ❌ Deploying to staging without first verifying on dev. The integration branch should already have green CI; staging is the production rehearsal.

## Related skills

- [[bmad-team-claim]] — claim before you start. Deploy never claims; it acts on already-merged work.
- [[bmad-team-verify]] (deferred) — remote integration test runs *before* a PR can be approved; this skill runs *after* merge.
