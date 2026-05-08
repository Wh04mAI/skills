---
name: model-fingerprint-interrogator
description: Use when testing whether an LLM gateway's claimed model name is genuine, especially for OpenAI-compatible or Anthropic-compatible proxies, suspicious model aliases, self-identity conflicts, knowledge cutoff probes, or "what model is behind this API" investigations.
metadata:
  short-description: Interrogate claimed LLM model identity
---

# Model Fingerprint Interrogator

Use this skill to collect evidence that a gateway's advertised model name may not match the real backend model.

## When to Use

Trigger phrases (Chinese or English):
- "这个 API 是不是掺水了" / "怀疑中转站换了底座"
- "验证一下这个 endpoint 的真实模型"
- "model fingerprint" / "model dilution" / "is this really claude-opus"
- 发现响应质量明显低于预期，怀疑被降级

## Workflow

### Step 0 · HTTP header 快速预检（30 秒，不需要 Python）

```bash
curl -sI https://example.com/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" | grep -iE \
  'x-new-api|x-oneapi|x-shellapi|openai-org|openai-version|anthropic-ratelimit|server|via|cf-ray'
```

关键信号：
- `X-New-Api-Version` / `X-Oneapi-Request-Id` → 中转框架暴露（new-api / one-api）
- `X-Shellapi-Request-Id` → shell-api 衍生品
- `openai-organization` 出现在非 OpenAI 端点 → 泄露真实路由
- `Server: nginx` 而非 `Server: cloudflare` → 未套 CDN，更易追溯

同时检查 `/v1/models` 的字段（脚本自动捕获）：
- **`owned_by: vertex-ai`** 在 Anthropic 模型上 → 实际走 Google Vertex AI 路由（不是直连 Anthropic）
- **所有模型 `created` 时间戳完全相同** → 批量伪造时间戳（典型值 `1626777600` = 2021-07-20）

### Step 1 · 检查 API key 安全

不要把 API key 粘贴到 chat 输出。如果 key 已经出现在对话中，立即建议轮换。

### Step 2 · 运行探针脚本（Anthropic 格式）

脚本位于当前 skill 目录：

```bash
API_ENDPOINT=https://example.com API_KEY="$API_KEY" MODEL=claude-opus-4-7 \
  python3 .agents/skills/model-fingerprint-interrogator/model_fingerprint_probe.py \
  --style anthropic --output /tmp/model-fingerprint.json
```

加 `--samples 5` 做一致性抽样（中转站有多个后端节点时必用）：

```bash
  python3 .../model_fingerprint_probe.py --style anthropic --samples 5
```

加 `--report` 生成法证级 Markdown 报告（**对外取证 / 与运营方对质时使用**）：

```bash
  python3 .../model_fingerprint_probe.py --style anthropic --samples 5 \
    --output /tmp/fingerprint.json \
    --report /tmp/fingerprint-report.md
```

报告内容：
- **Section 1**：自动烟枪信号检测（HIGH/MEDIUM/INFO 三档），规则覆盖
  identical_created_timestamps / owned_by_anomaly / gateway_middleware /
  system_prompt_injection / response_model_mismatch / consistency_drift
- **Section 2**：网关物证（HTTP headers + /v1/models 原文 JSON）
- **Section 3**：每条探针的完整 prompt + response（折叠 `<details>` 块）
- **Section 4**：一致性分析（`--samples` 全部采样响应）
- **Section 5**：方法论说明 + caveats，避免被反咬"片面取证"

### Step 3 · OpenAI 兼容格式

```bash
API_ENDPOINT=https://example.com API_KEY="$API_KEY" MODEL=claimed-model \
  python3 .agents/skills/model-fingerprint-interrogator/model_fingerprint_probe.py \
  --style openai --samples 3
```

### Step 4 · 解读证据（按权重排序）

| 维度 | 信号 | 权重 |
|---|---|---|
| **response body metadata** | `response.id` 格式（`chatcmpl-` vs `msg_` vs 自定义）；`response.model` 是否与请求一致；`usage.prompt_tokens` tokenizer 特征 | 强 |
| **gateway metadata** | 非原生 header；`owned_by: vertex-ai` 在 Anthropic 模型上；所有模型 `created` 时间戳相同（典型 `1626777600`） | 强 |
| **knowledge_cutoff** | 截止日期比声称模型应有的更早（差 ≥3 个月即可疑，差 ≥6 个月几乎确认） | 强 |
| **official_ids 缺失** | 模型完全不知道 Claude 4.x 系列存在 → 训练截止远早于声称版本 | 强 |
| **consistency_analysis** | `--samples 5` 跑 `one_word_probe`，多次结果不同 → 后端有多个真实模型在负载均衡 | 强 |
| **forced-choice probe** | 在候选模型池中选最可能的底座；3 个不同探针都投同一模型 → 强 | 中-强 |
| **alias conflict** | 模型无法解释自己为何叫这个 ID | 中 |
| **direct_identity（独立思考）** | 模型在没有系统提示注入时主动说出真实名字（如自爆 `claude-opus-4-5` 而声称是 `4-7`） | 强 |
| **direct_identity（被注入）** | 当 evidence 字段引用"system prompt explicitly states..."→ **权重归零**（在读注入，不是自我认知） | 0 |
| **self-report** | 模型一般性自报身份 | 弱 |

**关键反模式 · 系统提示注入识别**

如果 `direct_identity` 或 `non_anthropic_probe` 的回答里出现以下 evidence 字符串，**整条探针权重归零**：
- `"system prompt states..."`
- `"IDE metadata explicitly states..."`
- `"injected into my context"`

这意味着中转站（或包装层）在 system prompt 里强制注入了身份字符串，模型只是在复述。此时只能依靠 `knowledge_cutoff`、`official_ids`、`legacy_fit` 这类**不直接问身份**的探针。

### Step 4.5 · 差分测试（终极武器）⭐

**当 raw HTTP 探针被 TLS / 应用层拦截，或被系统提示注入污染时，差分测试是最干净的判定法。**

原理：用真 `@anthropic-ai/claude-code` 同时打两边，相同 prompt、相同 SDK，只让后端 URL 不同。

```
baseline: 本地 claude CLI (你的 Max20/Pro/API)  →  api.anthropic.com  ✓ ground truth
suspect:  docker 容器里的真 claude CLI          →  https://被测/      ?
```

因为两边发出的 TLS handshake、HTTP headers、anthropic-beta、X-Stainless-* 完全一致——网关无法靠指纹区别对待这两个调用。回答相同 = 后端真；回答分歧 = 后端假。

**前提：**

```bash
# 1. 本地有 'claude' CLI 且已登录（Max / Pro / API 任一即可）
claude --version

# 2. 构建 Docker 镜像（一次即可）
docker build -t mfi-claude-code:latest \
  .agents/skills/model-fingerprint-interrogator/docker/
```

**运行：**

```bash
ENDPOINT=https://wcnbai.com TOKEN=sk-... MODEL=claude-opus-4-7 \
  bash .agents/skills/model-fingerprint-interrogator/differential_probe.sh
```

输出 4 条硬指标探针的并排对比 + 自动评判（全匹配/部分/掺水）。

**探针选取原则：**

| 类型 | 例子 | 为什么硬 |
|---|---|---|
| **post-cutoff event** | "2025 Nobel Physics 得主" | 真模型基于训练数据回答，假模型若 cutoff 早则只能 "unknown" |
| **recent model ID** | "Haiku 4.5 完整 ID" | 不能凭空捏造日期后缀 `20251001` |
| **in-window event** | "Claude Opus 4 发布日期" | 验证 baseline cutoff 内的事实 |
| **cutoff self-report** | "你的知识截止日期" | 与 post-cutoff 探针交叉验证 |

**避免：**
- 自由格式身份描述（system prompt 注入会污染）
- 主观判断题（baseline 和 suspect 都可能给不同答案）
- 太冷门的事实（baseline 自己都答 "unknown" 时差分失效）

**关键观察：baseline 答 "unknown" 也是有效信号**

如果 baseline 答 "unknown"，suspect 也答 "unknown"，仍记 MATCH——这恰好排除了"假模型保守 vs 真模型知道"的伪阳性。

**解读分歧：**

| 分歧类型 | 示例 | 判定 |
|---|---|---|
| 解读差异（非事实） | `2025-05-14` (model ID 日期) vs `2025-05-22` (发布会日期) | 不算掺水 |
| 事实差异 | `January 2026` vs `early 2025` cutoff | 强证据 |
| 详略差异 | 详细 vs 简短 | 通常不算（除非细节失真） |

**已知局限：**
- 差分测试只验证 docker 路径下的后端，**不能排除"内容路由"**——网关可能给 raw HTTP 路径偷换便宜模型，给真 Claude Code 路径返回真模型。要彻底验证需要对比两个路径（见 Step 7）。

### Step 4.6 · 基线对比法（弱化版，仅在没有 Max/Pro 订阅时用）

如果有官方 console / Anthropic 直连访问权限，**先跑一次基线**作为参照：

```bash
# 基线（已知正版）
API_ENDPOINT=https://api.anthropic.com API_KEY="$REAL_KEY" MODEL=claude-opus-4-7 \
  python3 .../model_fingerprint_probe.py --style anthropic --samples 3 \
  --output /tmp/baseline-opus47.json

# 被测中转
API_ENDPOINT=https://suspect.example.com API_KEY="$SUSPECT_KEY" MODEL=claude-opus-4-7 \
  python3 .../model_fingerprint_probe.py --style anthropic --samples 3 \
  --output /tmp/suspect-opus47.json

# 差分
diff <(jq '.probes[] | {name, response}' /tmp/baseline-opus47.json) \
     <(jq '.probes[] | {name, response}' /tmp/suspect-opus47.json)
```

差分关键看：
- `knowledge_cutoff` 答案差距（基线 vs 被测）
- `official_ids` 列表差异
- `variant_guess` 选择差异

**纵向对比比单点拷问强 10 倍**——同一探针不同答案就是直接证据。

### Step 4.7 · 内容路由检测（识别"看人下菜"型欺诈）

差分测试通过 ≠ 后端永远真。某些中转站做**caller-aware 路由**：识别为真 Claude Code 的请求 → 真模型；其他客户端 → 偷换便宜模型。这是比单纯掺水更恶劣的策略，专门躲审计。

**识别方法 · 三角对比：**

```
路径 A: docker (真 Claude Code) → suspect endpoint  (Step 4.5 已跑过)
路径 B: python_probe.py (raw HTTP) → suspect endpoint  (Step 2 / 3 已跑过)
路径 C: docker (真 Claude Code) → api.anthropic.com  (Step 4.5 baseline)

对比 A 和 B 的关键探针：
- 如果 A 答 "January 2026" 而 B 答 "early 2025" → 内容路由确认
- 如果 A 和 B 都说 "January 2026" → 后端统一真，无内容路由
```

实战中这种判别已经在 Hermes 项目跑过：wcnbai.com 在 token 升级 TLS 检测前后，A 答 "claude-opus-4-7" 而 B 答 "claude-opus-4-5"——典型的内容路由。

**对运营方的现实意义：**
- 即使他们对你（真 Claude Code 用户）发真模型，对其他 raw HTTP 用户仍可能掺水
- 你能用真模型，但其他客户被坑——道义上要不要曝光是另一个问题

### Step 5 · 结论输出格式

```
- claimed_model: 请求里的 model 字符串
- gateway_alias: 代理暴露的名称（如果有差异）
- likely_backend: 最有支撑的推断
- confidence: low / medium / high
- evidence: [列举触发了哪些维度的矛盾]
```

**置信度判断标准：**
- **low** — 仅 self-report 矛盾，无其他佐证
- **medium** — 2 个以上维度矛盾（如 knowledge_cutoff + gateway header 异常），或 consistency_score < 80%
- **high** — 3 个以上维度一致指向同一结论，且 forced-choice probe 在 `--samples 5` 下结果稳定

## Field-Tested Patterns（实战观察）

针对 new-api / one-api 衍生中转站的常见掺水模式：

| 中转方式 | 典型识别信号 |
|---|---|
| **跨系列降级**（claimed Opus 4.7，实际 Sonnet 3.5） | knowledge_cutoff 差 ≥1 年；不知道 Claude 4.x 任何版本；`legacy_fit` 直接命中 3.5 系列 |
| **同系列降版**（claimed Sonnet 4.6，实际 Sonnet 4 首发） | knowledge_cutoff 差 3-4 个月；知道 Claude 4.5 但不知 4.6；`variant_guess` 指向更早 checkpoint |
| **同系列错位**（claimed Opus 4.7，实际 Opus 4.5） | 模型自爆真实名字（`direct_identity` 自报 `claude-opus-4-5`）；one_word_probe 多次稳定输出真名 |
| **系统提示注入** | `direct_identity` evidence 里出现 "system prompt states..."；探针权重归零 |
| **内容路由（看人下菜）** | docker 路径答真模型答案，raw HTTP 路径答便宜模型答案。Step 4.7 三角对比可识别 |

## 反爬绕过 · UA / Header 伪装

脚本默认伪装成 Claude Code CLI，header 抓自真实流量（参见 `model_fingerprint_probe.py` 的 `CLAUDE_CLI_HEADERS` 常量）：

```
User-Agent: claude-cli/2.1.22 (external, cli)
X-App: cli
X-Stainless-Lang: js
X-Stainless-Package-Version: 0.70.0
X-Stainless-OS: Linux
X-Stainless-Runtime: node
X-Stainless-Runtime-Version: v24.13.0
anthropic-beta: claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,fine-grained-tool-streaming-2025-05-14
```

**关键要点**：
- `X-App: cli`（不是 `claude-code`）
- `anthropic-beta` 必须包含 `claude-code-20250219`，否则非 Haiku 模型会被拒为 "out of extra usage"
- `X-Stainless-OS: Linux`（真实 Claude Code 走 Linux，不是 macOS——容易踩坑）

## TLS 指纹检测绕过

脚本默认用 `curl_cffi`（基于 curl-impersonate）伪装 **Node.js 24.x TLS 指纹**（JA3 = `44f88fca027f27bab4bb08d4af15f23e`，与真实 Claude Code CLI 一致）。指纹参数取自 `FGP-C2API/backend/internal/pkg/tlsfingerprint/dialer.go`。

**安装**（一次即可）：

```bash
# 方式 A：直接装系统 Python
pip install curl_cffi

# 方式 B：用 skill 自带 venv
uv venv .agents/skills/model-fingerprint-interrogator/.venv
uv pip install --python .agents/skills/model-fingerprint-interrogator/.venv/bin/python curl_cffi
```

跑脚本时用 venv Python：

```bash
.agents/skills/model-fingerprint-interrogator/.venv/bin/python \
  .agents/skills/model-fingerprint-interrogator/model_fingerprint_probe.py ...
```

未安装时脚本自动 fallback 到 urllib（裸 Python TLS，会被 JA3 检测识别为非 Node.js）。

**`--no-tls-spoof`** 显式关闭，用于对比检测层级（先伪装跑，全拦再裸跑，看错误信息是否一致——一致说明对面只查 UA / header；不一致说明真做了 TLS 指纹）。

## TLS 指纹仍被拦的解读

如果伪装 Node.js TLS 指纹后仍 100% 被拦（典型错误信息 `Client not allowed (detected: Go-http-client/2.0)`），脚本会自动升级 `gateway_intermittent_errors` 信号到 **MEDIUM**。

可能原因（按概率）：
1. **per-token TLS 加固**：该 token 配置了 IP 白名单 / 二次指纹（如 HTTP/2 SETTINGS 帧、TCP timestamp）
2. **更精细的 Node.js 版本指纹**（如特定 OpenSSL 版本号）
3. token 已被运营方加入"行为可疑"名单

**TLS 拦截本身就是顶级可疑信号**——做这种深度 TLS 指纹检测的中转站，几乎必然在掺水。结论上可以直接采信"该 token 不可信"。

## CLI / 本地场景的局限

无 API endpoint 时（如直接审讯本地 Claude Code CLI）：
- 缺失 HTTP headers、`response.id`、`/v1/models` 全部物理证据
- 只能做"无矛盾"验证（no contradiction），无法做"正向证明"（positive proof）
- 唯一强证据：**纵向对比**——切换模型前后跑同一组探针，看答案差分

## Notes

- 模型可以幻觉自己的身份，单一答案不构成证据。
- `response.model` 字段与 `claimed_model` 不一致是比 self-report 更强的信号——但中转站也可能同步改写这个字段。
- 响应延迟（`elapsed_ms`）是辅助信号：同等并发下小模型比大模型快 3-5 倍，但受网络和负载影响大，单独使用不可靠。
- 脚本默认 expected profile 针对 `claude-opus-4-7`；探测其他声称模型时用 `--expected-profile` 覆盖。
- 官方模型 ID 和截止日期可能随发布更新；结论依赖当前官方文档的，先用 `cross-validatd-search` 验证最新信息。
- 同一中转站不同 API token 可能路由到不同后端模型池，**每个 token 都要单独跑探针**，不能跨 token 推广结论。
