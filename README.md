# Wh04mAI Skills

Wh04mAI 团队共享的 Claude Code 技能。每个目录是一个独立的技能，可以单独安装。

## 有哪些技能

- **[`gh-board`](./gh-board)**：把 GitHub issue 和草稿 PR 当作任务看板，配合 BMAD 使用。负责把 epic 和故事发布成 issue、认领、转评审、放弃释放、合并后清理和查看进度。认领靠「同名分支只能被创建一次」来保证两个 agent 不会撞车，这一点在真实仓库上实测过。新仓库接入前要先做几项仓库设置，见技能里的「新仓库接入」一节。
- **[`priority-tags`](./priority-tags)**：管理 GitHub 仓库的优先级标签。初始化一套 MoSCoW 加紧急事故的标签，打标签时保证同一 issue 只有一个优先级，并做分诊和健康检查。和 `gh-board` 分工明确：它只管优先级标签，`gh-board` 只管状态标签。
- **[`claude-code-hook-design`](./claude-code-hook-design)**：设计和排查 Claude Code 里拦截危险命令的钩子，让它既拦得住，又不误伤正常命令。
- **[`agent-fallback-idempotency`](./agent-fallback-idempotency)**：长任务同时设了完成通知和兜底定时器时，让兜底唤醒先检查工作是否已经做完，避免重复执行。

## 安装

单个技能用 skills 命令行安装：

```bash
npx skills add Wh04mAI/skills@<技能名>
```

也可以克隆本仓库，把技能目录软链接到 Claude Code 的技能目录：

```bash
ln -s "$PWD/<技能名>" ~/.claude/skills/<技能名>
```

## 许可

MIT。
