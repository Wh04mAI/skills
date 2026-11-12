---
name: gh-board
description: 把 GitHub issue 和草稿 PR 当作 BMAD 的任务看板。负责把 epic 和故事发布成 issue、认领 issue、把做完的工作转成待评审、放弃时释放认领、合并后清理，以及查看当前进度。用户说「认领 #N」「claim #N」「发布故事」「把故事发到 GitHub」「放弃 #N」「释放 #N」「看板」「现在进度如何」「谁在做什么」时加载；bmad-build 等 BMAD 技能的定制步骤也会调用它。
---

# gh-board：用 GitHub 当 BMAD 的看板

## 分工

- 任务有哪些、谁在做、做到哪一步，只记在 GitHub 上：issue、子 issue、标签、负责人、草稿 PR。
- PRD、架构、epic 拆分、每个故事的 spec 这些内容，照旧是仓库里的文件。issue 正文只放摘要和指回文件的链接，不复制内容。
- 接入 gh-board 的项目不生成 `sprint-status.yaml`。BMAD 技能在这个文件不存在时会跳过进度同步，所以不要创建它，也不要去更新它。
- 优先级标签（`priority/*`）归 `priority-tags` 技能管，这里不碰。

## 新仓库接入

每个仓库接入时做一次，缺了前两项，认领就不安全或不会自动释放：

1. **禁止对认领分支强制推送。** 认领互斥靠的是「同名分支只能被创建一次」，有人强制推送就能悄悄抢走别人的认领。用仓库规则集禁掉，绕过名单留空。agent 用的就是你本人的账号，给管理员开绕过等于没禁：

   ```bash
   gh api -X POST repos/<owner>/<repo>/rulesets --input - <<'EOF'
   {"name": "claim-branches-no-force-push", "target": "branch", "enforcement": "active", "bypass_actors": [],
    "conditions": {"ref_name": {"include": ["refs/heads/claim/**"], "exclude": []}},
    "rules": [{"type": "non_fast_forward"}]}
   EOF
   ```

   私有仓库要组织是 Team 及以上套餐，规则集才会生效。
2. **合并后自动删除分支。** 删除分支就是释放认领，不打开的话，PR 合并后认领会一直挂着：`gh api -X PATCH repos/<owner>/<repo> -F delete_branch_on_merge=true`。
3. **默认分支设成开发集成分支。** 认领分支从仓库默认分支拉出，也合回默认分支。只有 `main` 一条长期分支的仓库不用改。如果仓库用 `dev`、`staging`、`main` 这类对应部署环境的分支，要把日常开发合进去的那条（通常是 `dev`）设成默认分支：PR 正文里的 `Closes #N` 只有合进默认分支时才会自动关闭 issue。这时还要用规则集禁止删除这几条环境分支，否则开了第 2 项之后，晋级 PR 一合并，源分支就会被自动删掉。
4. **忽略 worktree 目录。** 在 `.gitignore` 里加一行 `.worktrees/`。
5. **初始化标签。** 见下面的「初始化标签」。
6. **项目用 BMAD 的话，复制定制文件。** 把本技能 `assets/bmad-custom/` 下的三个文件复制到项目的 `_bmad/custom/`。它们让 `bmad-build` 开工前检查认领、做完后问是否转评审，让拆完故事后问是否发布成 issue，并拦下只会生成 `sprint-status.yaml` 的 `bmad-sprint-planning`。

## 约定

**标签。** 类型标签有 `epic`、`story`、`spike`、`bug`。状态标签有 `backlog`、`in-progress`、`review`。另有 `hitl`，表示这个任务需要人参与才能推进。完成的任务直接关闭 issue；放弃的任务以「不计划做」的理由关闭。这套映射和 BMAD v7 的 GitHub 存储一致，将来迁到 v7 时可以原样保留。

**层级和依赖。** epic 是一个 issue，它的故事是子 issue。故事之间的先后顺序用「被谁阻塞」关系表示。

**认领。** 认领分支名只由 issue 号决定，固定为 `claim/issue-<N>`，不带 agent 标识。认领者写在分支的第一个提交里。远端有这个分支，就表示 issue 已被认领；删除远端分支就是释放认领。

**worktree。** 每个认领对应一个 worktree，位置固定在仓库根目录下的 `.worktrees/issue-<N>`，这个目录已在 `.gitignore` 里忽略。认领后，这个 issue 的所有改动都在这个 worktree 里完成。

**分支上只追加，不改写。** 接入时加的规则集禁止对 `claim/**` 强制推送，这是认领互斥的保障，不要试图绕开。写错了就追加修正提交；要跟上默认分支就把它合并进来，不要 rebase。最终用压缩合并进默认分支，分支上的中间提交不会留下。

## 动作

### 初始化标签（每个仓库做一次）

列出仓库已有标签，缺哪个建哪个。报「已存在」就跳过，不要用 `--force`，它会改掉已有标签的颜色和说明。

```bash
gh label list --limit 1000 --json name --jq '.[].name'
gh label create <名字> --description "<一句说明>"
```

需要的标签：`epic`、`story`、`spike`、`bug`、`backlog`、`in-progress`、`review`、`hitl`。

### 发布 epic 和故事

来源是 `bmad-create-epics-and-stories` 生成的 `epics.md`。发布会在 GitHub 上批量建 issue，开始前先把要建的清单给用户看，得到同意再动手。

1. 每个 epic 建一个 issue，打 `epic` 和 `backlog` 标签。
2. 每个故事建一个子 issue，打 `story` 和 `backlog` 标签：先不带关系创建，拿到 issue 号之后，再用 `gh issue edit <N> --parent <epic号>` 挂到 epic 下，用 `--add-blocked-by` 补上依赖。创建和加关系分两步做，是因为加关系失败时 `gh` 会报错且不打印新 issue 的地址，重跑就会建出重复的 issue。
3. 每个 issue 正文末尾加一行编号标记，epic 写 `BMAD: epic 1`，故事写 `BMAD: story 1.2`，再加一行指向 `epics.md` 里对应小节的链接。
4. 发布前先查重：`gh issue list --state all --limit 1000 --json number,body`，正文里已有同一编号标记的就跳过，不要重复创建。

### 认领

```bash
bash {skill-root}/scripts/claim.sh <issue号> [agent标识]
```

agent 标识默认是 `claude@<短主机名>`，同一台机器上同时开多个会话时，给每个会话传一个不同的标识。脚本做的事，按顺序：

1. 确认 issue 存在且未关闭。
2. 从仓库默认分支的最新提交建 `.worktrees/issue-<N>` 和本地分支 `claim/issue-<N>`，做一个写着认领者的空提交。
3. 用「只在远端不存在该分支时才创建」的方式推送。这一步决定谁拿到认领，GitHub 服务端保证同时推送时只有一个成功。
4. 开草稿 PR，标题为「issue 标题 (#N)」，正文含 `Closes #N`。
5. 把 issue 指派给当前 GitHub 账号，状态标签换成 `in-progress`。

认领成功后，告诉用户在 worktree 里新开一个会话开工（`cd .worktrees/issue-<N> && claude`），不要在当前会话里接着跑 `bmad-build`。原因是 BMAD 技能启动时会把文件路径固定成当时所在目录的绝对路径，在主目录启动的 `bmad-build` 会把 spec 写回主目录，而不是 worktree。

退出码：`0` 表示认领成功；`2` 表示已被别人认领，脚本会打印认领者，此时停止，不要接手别人的认领；`1` 是其他错误。第 4、5 步失败时认领本身已经成立，脚本照样返回 `0` 并打印警告，用同样的参数重跑一次就会补齐。

### 转评审

在 worktree 里推送最新提交，把草稿 PR 转成正式 PR，状态标签换成 `review`：

```bash
git -C .worktrees/issue-<N> push origin HEAD:refs/heads/claim/issue-<N>
gh pr ready claim/issue-<N>
gh issue edit <N> --remove-label in-progress --add-label review
```

### 放弃（释放认领）

只能释放自己的认领。先移除 worktree，再关闭 PR，并同时删除本地和远端分支。顺序不能反：分支还被 worktree 占着时删不掉。最后把 issue 退回 `backlog` 并取消指派：

```bash
git worktree remove .worktrees/issue-<N>
gh pr close claim/issue-<N> --delete-branch --comment "放弃：<原因>"
gh issue edit <N> --remove-label in-progress --remove-label review --add-label backlog --remove-assignee @me
```

`git worktree remove` 遇到未提交的改动会拒绝执行。这时先问用户这些改动要不要，不要直接加 `--force`。

### 合并之后清理

压缩合并后，GitHub 会自动关闭 issue（因为 PR 正文里有 `Closes #N`），并自动删除远端分支，认领随之释放。本地还要清理：

```bash
git worktree remove .worktrees/issue-<N>
git branch -D claim/issue-<N>
```

### 查看进度

```bash
gh issue list --label in-progress --json number,title,assignees
gh issue list --label review --json number,title,assignees
gh issue list --label backlog --label story --json number,title
gh pr list --search "head:claim/" --json number,title,isDraft,headRefName
```

回答「下一个能做什么」时，从 `backlog` 的故事里挑没有未关闭阻塞项的。阻塞关系这样查，`state` 为 `OPEN` 的就是还没解除的阻塞：

```bash
gh issue view <N> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'
```

`gh issue list` 按标签筛选时有几秒延迟，刚改完标签马上列，可能还是旧结果。要确认某个 issue 的当前状态，用 `gh issue view <N>` 直接查，它是即时的。epic 的 `subIssuesSummary` 计数同样有延迟，要核对子 issue 就看 `subIssues.nodes`。
