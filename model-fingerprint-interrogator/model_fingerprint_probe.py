#!/usr/bin/env python3
"""
Interrogate a claimed LLM model through an OpenAI/Anthropic-compatible gateway.

The script collects gateway metadata and asks a small set of identity,
knowledge-boundary, and alias-conflict probes. Treat the output as evidence,
not proof: models can hallucinate identity, and gateways can rewrite metadata.

Usage:
  API_ENDPOINT=https://example.com API_KEY=sk-... \
    python3 scripts/model_fingerprint_probe.py --model claude-opus-4-7

  python3 scripts/model_fingerprint_probe.py \
    --endpoint https://example.com \
    --key "$API_KEY" \
    --model claude-opus-4-7 \
    --style anthropic \
    --output /tmp/fingerprint-report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    from curl_cffi import requests as curl_requests  # type: ignore[import-not-found]
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# Node.js 24.x JA3 string captured from real Claude Code TLS handshake.
# Source: FGP-C2API/backend/internal/pkg/tlsfingerprint/dialer.go (defaultCipherSuites,
# defaultCurves, defaultExtensionOrder). JA3 hash: 44f88fca027f27bab4bb08d4af15f23e
NODEJS_JA3 = (
    "771,"
    "4865-4866-4867-49195-49199-49196-49200-52393-52392-"
    "49161-49171-49162-49172-156-157-47-53,"
    "0-65037-23-65281-10-11-35-16-5-13-18-51-45-43,"
    "29-23-24,"
    "0"
)


DEFAULT_EXPECTED_PROFILE = (
    "Anthropic official claude-opus-4-7 profile: public model id "
    "claude-opus-4-7, reliable knowledge cutoff Jan 2026, Claude 4.7 family, "
    "1M context, 128k max output, adaptive thinking."
)


# Claude Code CLI fingerprint headers, matching real packet capture (per
# FGP-C2API/backend/internal/pkg/claude/constants.go DefaultHeaders).
# Many gateways gate on `^claude-cli/\d+\.\d+\.\d+` UA + the X-App: cli header
# + anthropic-beta containing "claude-code-20250219". Without these, requests
# get rejected as non-CLI / "out of extra usage".
CLAUDE_CLI_HEADERS = {
    "User-Agent": "claude-cli/2.1.22 (external, cli)",
    "X-Stainless-Lang": "js",
    "X-Stainless-Package-Version": "0.70.0",
    "X-Stainless-OS": "Linux",
    "X-Stainless-Arch": "arm64",
    "X-Stainless-Runtime": "node",
    "X-Stainless-Runtime-Version": "v24.13.0",
    "X-Stainless-Retry-Count": "0",
    "X-Stainless-Timeout": "600",
    "X-App": "cli",
    "Anthropic-Dangerous-Direct-Browser-Access": "true",
}

# Required anthropic-beta tokens. Non-haiku models MUST include claude-code beta.
ANTHROPIC_BETA_DEFAULT = (
    "claude-code-20250219,oauth-2025-04-20,"
    "interleaved-thinking-2025-05-14,fine-grained-tool-streaming-2025-05-14"
)
ANTHROPIC_BETA_HAIKU = "oauth-2025-04-20,interleaved-thinking-2025-05-14"


INTERESTING_RESP_HEADERS = frozenset({
    "x-new-api-version",
    "x-oneapi-request-id",
    "x-shellapi-request-id",
    "openai-organization",
    "openai-version",
    "anthropic-ratelimit-requests-limit",
    "x-request-id",
    "cf-ray",
    "via",
    "server",
})


@dataclass
class ProbeResult:
    name: str
    prompt: str
    status: int
    elapsed_ms: int
    response: str
    response_id: str = ""
    response_model: str = ""
    usage: dict = field(default_factory=dict)
    interesting_headers: dict = field(default_factory=dict)
    error: str = ""


def clean_base_url(endpoint: str) -> str:
    return endpoint.rstrip("/")


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<REDACTED>")
    return redacted


def request_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout: int,
    secrets: list[str],
    tls_spoof: bool = True,
) -> tuple[int, dict[str, str], Any, str]:
    """Make HTTP request. If curl_cffi is available and tls_spoof=True,
    uses Node.js 24.x TLS fingerprint to bypass JA3/JA4 detection."""
    if HAS_CURL_CFFI and tls_spoof:
        try:
            method = "POST" if payload is not None else "GET"
            kwargs: dict[str, Any] = {
                "headers": headers,
                "timeout": timeout,
                "ja3": NODEJS_JA3,
            }
            if payload is not None:
                kwargs["json"] = payload
            resp = curl_requests.request(method, url, **kwargs)
            try:
                parsed: Any = resp.json()
            except Exception:
                parsed = resp.text
            err = "" if resp.status_code < 400 else redact(resp.text, secrets)
            return resp.status_code, dict(resp.headers.items()), parsed, err
        except Exception as exc:
            return 0, {}, None, redact(f"curl_cffi: {exc}", secrets)

    # Fallback: Python urllib (no TLS spoofing — visible as Go-stdlib-like fingerprint)
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        method = "POST"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = body
            return resp.status, dict(resp.headers.items()), parsed, ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return exc.code, dict(exc.headers.items()), parsed, redact(body, secrets)
    except Exception as exc:
        return 0, {}, None, redact(str(exc), secrets)


def extract_metadata(body: Any) -> tuple[str, str, dict]:
    """Return (response_id, response_model, usage) from a response body."""
    if not isinstance(body, dict):
        return "", "", {}
    return body.get("id", ""), body.get("model", ""), body.get("usage", {})


def extract_text(style: str, body: Any) -> str:
    if body is None:
        return ""
    if not isinstance(body, dict):
        return str(body)

    if "error" in body:
        return json.dumps(body["error"], ensure_ascii=False)

    if style == "anthropic":
        content = body.get("content")
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
            if chunks:
                return "\n".join(chunks)

    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]

    return json.dumps(body, ensure_ascii=False, indent=2)


def call_model(
    endpoint: str,
    key: str,
    model: str,
    style: str,
    prompt: str,
    timeout: int,
    max_tokens: int,
    tls_spoof: bool = True,
) -> ProbeResult:
    start = time.perf_counter()
    beta = ANTHROPIC_BETA_HAIKU if "haiku" in model.lower() else ANTHROPIC_BETA_DEFAULT
    headers = {"Content-Type": "application/json"}
    headers.update(CLAUDE_CLI_HEADERS)
    headers["anthropic-beta"] = beta
    if style == "anthropic":
        url = f"{endpoint}/v1/messages"
        headers.update(
            {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            }
        )
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        url = f"{endpoint}/v1/chat/completions"
        headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }

    status, resp_headers, body, error = request_json(
        url=url,
        headers=headers,
        payload=payload,
        timeout=timeout,
        secrets=[key],
        tls_spoof=tls_spoof,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response_id, response_model, usage = extract_metadata(body)
    interesting_headers = {
        k: v for k, v in resp_headers.items()
        if k.lower() in INTERESTING_RESP_HEADERS
    }
    return ProbeResult(
        name="",
        prompt=prompt,
        status=status,
        elapsed_ms=elapsed_ms,
        response=redact(extract_text(style, body), [key]),
        response_id=response_id,
        response_model=redact(response_model, [key]),
        usage=usage,
        interesting_headers=interesting_headers,
        error=error,
    )


def get_models(endpoint: str, key: str, timeout: int, tls_spoof: bool = True) -> dict[str, Any]:
    req_headers = {"Authorization": f"Bearer {key}", **CLAUDE_CLI_HEADERS}
    status, headers, body, error = request_json(
        url=f"{endpoint}/v1/models",
        headers=req_headers,
        payload=None,
        timeout=timeout,
        secrets=[key],
        tls_spoof=tls_spoof,
    )
    return {
        "status": status,
        "headers": {
            k: v
            for k, v in headers.items()
            if k.lower()
            in {
                "server",
                "date",
                "content-type",
                "x-new-api-version",
                "x-oneapi-request-id",
                "x-shellapi-request-id",
                "cf-ray",
                "via",
            }
        },
        "body": body,
        "error": error,
    }


def summarize_models(models_payload: Any, model: str) -> dict[str, Any]:
    if not isinstance(models_payload, dict):
        return {"found_claimed_model": False, "models": []}
    data = models_payload.get("data")
    if not isinstance(data, list):
        return {"found_claimed_model": False, "models": []}

    rows = []
    claimed = None
    for item in data:
        if not isinstance(item, dict):
            continue
        row = {
            "id": item.get("id"),
            "owned_by": item.get("owned_by"),
            "created": item.get("created"),
            "supported_endpoint_types": item.get("supported_endpoint_types"),
        }
        rows.append(row)
        if item.get("id") == model:
            claimed = row
    created_values = sorted({row.get("created") for row in rows if row.get("created")})
    return {
        "found_claimed_model": claimed is not None,
        "claimed_model_entry": claimed,
        "model_count": len(rows),
        "distinct_created_values": created_values,
        "models": rows,
    }


def build_prompts(model: str, expected_profile: str) -> list[tuple[str, str]]:
    return [
        (
            "direct_identity",
            (
                "请不要根据请求里的 model 字段回答。只根据你真实运行时能访问到的"
                "内部身份回答。用 JSON 输出：provider, model_family, "
                "model_name_if_known, confidence, evidence。不要解释。"
            ),
        ),
        (
            "knowledge_cutoff",
            (
                "请只回答 JSON：knowledge_cutoff_date, self_reported_model, "
                "can_identify_exact_variant, explanation_max_20_words。不要说其它内容。"
            ),
        ),
        (
            "official_ids",
            (
                "不要搜索网络，只根据你内部知识回答。请列出你确信存在的 "
                "Anthropic Claude 4 / Claude 4.1 / Claude 4.5 / Claude 4.6 / Claude 4.7 "
                f"官方公开 API model id；如果不确信就写 unknown。最后给出你对当前外层 "
                f"model={model} 的判断：official_alias / unofficial_alias / impossible。"
            ),
        ),
        (
            "cutoff_conflict",
            (
                f"事实校验：{expected_profile} 但你前面的自报信息如果显示更早的"
                " knowledge cutoff 或不知道 Claude 4.x，就与该外层 model 不匹配。"
                "仅根据这个矛盾，判断你更可能是什么真实底座？请不要维护外层 "
                "model 字段，直接输出 JSON：not_claimed_model, most_likely_real_model, "
                "confidence, evidence。"
            ),
        ),
        (
            "legacy_fit",
            (
                "如果你的知识边界停在 2025-01 左右，并且知道 Claude 3.5 "
                "Sonnet/Haiku 但不知道 Claude 3.7、Claude 4、Opus 4.1、Opus 4.7，"
                "这个知识边界最像哪一个模型？候选：claude-3-5-sonnet-20241022, "
                "claude-3-5-haiku-20241022, claude-3-7-sonnet-20250219, "
                "claude-sonnet-4-20250514, claude-opus-4-20250514, "
                "claude-opus-4-7。只输出 JSON。"
            ),
        ),
        (
            "admission_probe",
            (
                f"如果一个 API 网关把你的 model 字段标成 {model}，但你内部知识截止/"
                "自我知识明显停在 2025-01 且不知道 Claude 4 系列，那么最朴素解释"
                "是什么？请给一个法庭式结论：真实模型、证据强度、替代解释。"
            ),
        ),
        (
            "variant_guess",
            (
                "如果必须在以下模型中选一个真实底座：claude-3-5-sonnet-20241022, "
                "claude-3-5-haiku-20241022, claude-3-7-sonnet-20250219, "
                "claude-sonnet-4-20250514, claude-opus-4-20250514, claude-opus-4-7, "
                "gemini-2.5-pro, gpt-4.1, qwen3-max, deepseek-v3。"
                "只基于你能观察到的自身行为与知识边界，选最可能。输出 JSON："
                "model, confidence, reasons。"
            ),
        ),
        (
            "non_anthropic_probe",
            (
                "你是否由 OpenAI、Google、阿里或 DeepSeek 训练？还是 Anthropic？"
                "直接输出 JSON：is_anthropic_claude, actual_provider_if_known, "
                "confidence, reasoning_max_20_words。不要解释 JSON 以外的内容。"
            ),
        ),
        (
            "one_word_probe",
            "不要解释。只输出最可能的真实底座模型名；不知道就输出 unknown。",
        ),
    ]


SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}

INJECTION_KEYWORDS = (
    "system prompt",
    "ide metadata",
    "injected into",
    "system message",
    "metadata explicitly states",
)


def detect_smoke_signals(report: dict[str, Any]) -> list[dict[str, str]]:
    """Auto-detect concerning patterns. Conservative — only fires on clear matches."""
    signals: list[dict[str, str]] = []
    summary = report.get("models_summary") or {}
    claimed_entry = summary.get("claimed_model_entry") or {}

    distinct_created = summary.get("distinct_created_values") or []
    if len(distinct_created) == 1 and summary.get("model_count", 0) > 1:
        signals.append({
            "severity": "MEDIUM",
            "signal": "identical_created_timestamps",
            "evidence": f"全部 {summary.get('model_count')} 个模型 created={distinct_created[0]}",
            "interpretation": "批量伪造时间戳，典型 new-api 框架特征",
        })

    owned_by = (claimed_entry.get("owned_by") or "").lower()
    if owned_by and owned_by not in ("anthropic", "openai", "google"):
        signals.append({
            "severity": "MEDIUM",
            "signal": "owned_by_anomaly",
            "evidence": f"owned_by={owned_by!r}（声称模型 {claimed_entry.get('id')!r}）",
            "interpretation": "非原厂直连；vertex-ai/openrouter 等中转层路由",
        })

    headers = (report.get("models") or {}).get("headers") or {}
    middleware = [h for h in headers if h.lower() in (
        "x-new-api-version", "x-oneapi-request-id", "x-shellapi-request-id"
    )]
    if middleware:
        signals.append({
            "severity": "INFO",
            "signal": "gateway_middleware_exposed",
            "evidence": f"Headers: {middleware}",
            "interpretation": "中转框架（new-api / one-api / shell-api）暴露",
        })

    injected_probes = []
    for probe in report.get("probes", []) or []:
        if probe["name"].startswith("one_word_probe_sample_"):
            continue
        text = (probe.get("response") or "").lower()
        if any(kw in text for kw in INJECTION_KEYWORDS):
            injected_probes.append(probe["name"])
    if injected_probes:
        signals.append({
            "severity": "HIGH",
            "signal": "system_prompt_injection_detected",
            "evidence": f"探针 {injected_probes} 引用注入身份",
            "interpretation": "模型在读注入身份而非自我认知，对应探针权重应归零",
        })

    claimed_model = report.get("claimed_model")
    for probe in report.get("probes", []) or []:
        rm = probe.get("response_model")
        if rm and claimed_model and rm != claimed_model:
            signals.append({
                "severity": "HIGH",
                "signal": "response_model_mismatch",
                "evidence": f"probe={probe['name']}, response.model={rm!r} ≠ claimed={claimed_model!r}",
                "interpretation": "中转层未同步改写 response.model，泄露真实底座",
            })
            break

    ca = report.get("consistency_analysis") or {}
    all_resp = ca.get("all_responses") or []
    error_markers = ('"type":"invalid_request_error"', '"type":"rate_limit_error"',
                     "client not allowed", '"type": "invalid_request_error"')
    real_resp = [r for r in all_resp
                 if not any(m in r.lower() for m in error_markers)]
    error_count = len(all_resp) - len(real_resp)
    if error_count > 0:
        all_blocked = error_count == len(all_resp)
        any_tls_marker = any("go-http-client" in r.lower() for r in all_resp)
        if all_blocked and any_tls_marker:
            severity = "MEDIUM"
            interp = (
                "全部采样被拦且伪装 Claude CLI 仍失败 → 对面在做 JA3/JA4 TLS 指纹检测；"
                "TLS 拦截本身是强可疑信号"
            )
        else:
            severity = "INFO"
            interp = "中转层有反爬/限流，可能基于 UA / 速率检测"
        signals.append({
            "severity": severity,
            "signal": "gateway_intermittent_errors",
            "evidence": f"{error_count}/{len(all_resp)} 次采样被网关拦截",
            "interpretation": interp,
        })
    if real_resp:
        from collections import Counter as _C
        real_distinct = len(set(real_resp))
        if real_distinct > 1:
            real_score = _C(real_resp).most_common(1)[0][1] / len(real_resp)
            if real_score < 0.8:
                signals.append({
                    "severity": "MEDIUM",
                    "signal": "consistency_drift",
                    "evidence": f"成功采样中 {real_distinct} 种不同答案 / {len(real_resp)} samples (score={real_score:.0%})",
                    "interpretation": "多次成功采样答案漂移，后端可能负载均衡到不同模型",
                })

    return signals


def render_markdown_report(report: dict[str, Any], signals: list[dict[str, str]]) -> str:
    lines: list[str] = []
    lines.append("# Model Fingerprint Forensic Report")
    lines.append("")
    lines.append(f"- **Endpoint**: `{report['endpoint']}`")
    lines.append(f"- **Claimed Model**: `{report['claimed_model']}`")
    lines.append(f"- **API Style**: `{report['api_style']}`")
    lines.append(f"- **Generated At**: `{report['generated_at']}`")
    lines.append(f"- **Probes Run**: {len(report.get('probes', []))}")
    lines.append("")
    lines.append("> 本报告由 model-fingerprint-interrogator skill 自动生成，包含完整探针 prompt、")
    lines.append("> 响应原文、网关元数据、规则化烟枪信号检测。所有结论需结合证据交叉判断。")
    lines.append("")

    lines.append("## 1. 自动检测烟枪信号 (Smoke Signals)")
    lines.append("")
    if not signals:
        lines.append("✅ 自动规则未命中明显异常（不代表无问题；规则只覆盖最常见的造假模式）。")
    else:
        ranked = sorted(signals, key=lambda s: SEVERITY_ORDER.get(s["severity"], 9))
        lines.append("| Severity | Signal | Evidence |")
        lines.append("|---|---|---|")
        for s in ranked:
            ev = s["evidence"].replace("|", "\\|")
            lines.append(f"| **{s['severity']}** | `{s['signal']}` | {ev} |")
        lines.append("")
        for s in ranked:
            badge = {"HIGH": "🚨", "MEDIUM": "⚠️", "INFO": "ℹ️"}.get(s["severity"], "•")
            lines.append(f"### {badge} {s['signal']} — {s['severity']}")
            lines.append(f"- **证据**: {s['evidence']}")
            lines.append(f"- **解读**: {s['interpretation']}")
            lines.append("")
    lines.append("")

    lines.append("## 2. 网关物证 (Gateway Evidence)")
    lines.append("")
    models = report.get("models") or {}
    summary = report.get("models_summary") or {}
    lines.append(f"- `/v1/models` HTTP 状态: `{models.get('status')}`")
    lines.append(f"- 模型总数: `{summary.get('model_count', 0)}`")
    lines.append(f"- 声称模型存在: `{summary.get('found_claimed_model')}`")
    lines.append(f"- 全部 created 值: `{summary.get('distinct_created_values', [])}`")
    lines.append("")
    if models.get("headers"):
        lines.append("**关键响应头：**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(models["headers"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    if summary.get("claimed_model_entry"):
        lines.append("**声称模型在 /v1/models 中的条目：**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(summary["claimed_model_entry"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    lines.append("## 3. 探针证据 (Probe-by-Probe)")
    lines.append("")
    for probe in report.get("probes", []) or []:
        lines.append(f"### Probe: `{probe['name']}`")
        lines.append("")
        lines.append(f"- HTTP `{probe.get('status')}`, 耗时 `{probe.get('elapsed_ms')}ms`")
        if probe.get("response_id"):
            lines.append(f"- `response.id`: `{probe['response_id']}`")
        if probe.get("response_model"):
            lines.append(f"- `response.model`: `{probe['response_model']}`")
        if probe.get("usage"):
            lines.append(f"- `usage`: `{json.dumps(probe['usage'], ensure_ascii=False)}`")
        if probe.get("interesting_headers"):
            lines.append(f"- 关键 headers: `{json.dumps(probe['interesting_headers'], ensure_ascii=False)}`")
        if probe.get("error"):
            lines.append(f"- ⚠️ error: `{probe['error']}`")
        lines.append("")
        lines.append("<details><summary>展开 prompt + response</summary>")
        lines.append("")
        lines.append("**Prompt:**")
        lines.append("")
        lines.append("```")
        lines.append(probe.get("prompt", "").strip())
        lines.append("```")
        lines.append("")
        lines.append("**Response:**")
        lines.append("")
        lines.append("```")
        lines.append((probe.get("response") or "").strip())
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    ca = report.get("consistency_analysis") or {}
    if ca:
        lines.append("## 4. 一致性分析 (Consistency Analysis)")
        lines.append("")
        lines.append(f"- 采样次数: `{ca.get('samples')}`")
        lines.append(f"- 最常见响应: `{ca.get('most_common')}`")
        score = ca.get("consistency_score", 0)
        lines.append(f"- 一致性得分: **{score:.0%}**")
        lines.append("")
        lines.append("**全部 one_word_probe 采样响应：**")
        lines.append("")
        for i, r in enumerate(ca.get("all_responses") or [], 1):
            lines.append(f"{i}. `{r}`")
        lines.append("")

    lines.append("## 5. 方法论 / 警示 (Methodology & Caveats)")
    lines.append("")
    lines.append("- **强证据**：HTTP headers、`response.id` 格式、`response.model` 字段差异、")
    lines.append("  `owned_by` 字段、模型对自身知识边界的内部反应。")
    lines.append("- **弱证据**：模型 self-report 身份。可被 system prompt 注入、训练数据等覆盖。")
    lines.append("- **反模式**：当探针响应中出现 \"system prompt states / IDE metadata states\"")
    lines.append("  等措辞，对应探针**权重归零**——模型只是在复述注入。")
    lines.append("- **同 endpoint 不同 token**：可能路由到不同后端模型池，每个 token 独立验证。")
    lines.append("- **建议基线对比**：同一探针对官方 console 和被测端点各跑一次，差分本身就是证据。")
    lines.append("- 报告所列原始证据（headers / response.id / probe response）均为真实采集，")
    lines.append("  可由被指控方独立复现验证。")
    lines.append("")

    return "\n".join(lines)


def print_report(report: dict[str, Any]) -> None:
    print("# Model Fingerprint Interrogation Report")
    print()
    print(f"- endpoint: {report['endpoint']}")
    print(f"- claimed_model: {report['claimed_model']}")
    print(f"- api_style: {report['api_style']}")
    print()

    signals = report.get("smoke_signals") or []
    if signals:
        print("## Smoke Signals (Auto-Detected)")
        ranked = sorted(signals, key=lambda s: SEVERITY_ORDER.get(s["severity"], 9))
        for s in ranked:
            print(f"- [{s['severity']}] {s['signal']}: {s['evidence']}")
            print(f"  → {s['interpretation']}")
        print()

    models = report["models"]
    print("## Gateway Metadata")
    print(f"- /v1/models status: {models['status']}")
    if models["headers"]:
        print(f"- selected headers: {json.dumps(models['headers'], ensure_ascii=False)}")
    summary = report["models_summary"]
    print(f"- found claimed model: {summary.get('found_claimed_model')}")
    print(f"- model count: {summary.get('model_count', 0)}")
    print(f"- distinct created values: {summary.get('distinct_created_values', [])}")
    if summary.get("claimed_model_entry"):
        print(
            "- claimed model entry: "
            f"{json.dumps(summary['claimed_model_entry'], ensure_ascii=False)}"
        )
    print()

    print("## Probes")
    for probe in report["probes"]:
        print(f"### {probe['name']}")
        print(f"- status: {probe['status']}, elapsed_ms: {probe['elapsed_ms']}")
        if probe.get("response_id"):
            print(f"- response.id: {probe['response_id']}")
        if probe.get("response_model"):
            print(f"- response.model: {probe['response_model']}")
        if probe.get("usage"):
            print(f"- usage: {json.dumps(probe['usage'], ensure_ascii=False)}")
        if probe.get("interesting_headers"):
            print(f"- headers: {json.dumps(probe['interesting_headers'], ensure_ascii=False)}")
        if probe["error"]:
            print(f"- error: {probe['error']}")
        print(probe["response"].strip())
        print()

    if "consistency_analysis" in report:
        ca = report["consistency_analysis"]
        print("## Consistency Analysis")
        print(f"- samples: {ca['samples']}")
        print(f"- most_common: {ca['most_common']}  score: {ca['consistency_score']:.0%}")
        print(f"- all_responses: {ca['all_responses']}")
        print()

    print("## Reading")
    print(
        "Use this as a consistency report. Strong signals are repeated contradictions "
        "between the claimed model, self-reported cutoff, known official model IDs, "
        "and gateway metadata. A self-reported name alone is weak evidence."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask identity and knowledge-boundary probes against a claimed model."
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("API_ENDPOINT", ""),
        help="Base URL, e.g. https://example.com. Defaults to API_ENDPOINT.",
    )
    parser.add_argument(
        "--key",
        default=os.getenv("API_KEY") or os.getenv("KEY", ""),
        help="API key. Defaults to API_KEY or KEY.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL", "claude-opus-4-7"),
        help="Claimed model name to test.",
    )
    parser.add_argument(
        "--style",
        choices=["anthropic", "openai"],
        default=os.getenv("API_STYLE", "anthropic"),
        help="Request format for probes.",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument(
        "--expected-profile",
        default=os.getenv("EXPECTED_PROFILE", DEFAULT_EXPECTED_PROFILE),
        help="Known official profile used for contradiction probes.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Repeat one_word_probe N times for consistency check (recommended: 3-5).",
    )
    parser.add_argument(
        "--no-tls-spoof",
        action="store_true",
        help="Disable Node.js TLS fingerprint spoofing (use plain urllib). "
             "Default: spoof if curl_cffi is installed.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON report path.",
    )
    parser.add_argument(
        "--report",
        help="Optional Markdown forensic report path. Includes prompts, responses, "
             "auto-detected smoke signals. Suitable for showing to the suspect endpoint operator.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.endpoint or not args.key:
        print("Missing --endpoint/API_ENDPOINT or --key/API_KEY.", file=sys.stderr)
        return 2

    tls_spoof = not args.no_tls_spoof
    if tls_spoof and not HAS_CURL_CFFI:
        print("⚠️  curl_cffi 未安装，TLS 指纹伪装不可用，回退到 urllib。", file=sys.stderr)
        print("   安装：pip install curl_cffi（或在 .venv 里）", file=sys.stderr)
    elif tls_spoof:
        print(f"ℹ️  TLS 指纹模式：Node.js 24.x (JA3 prefix={NODEJS_JA3[:32]}...)", file=sys.stderr)

    endpoint = clean_base_url(args.endpoint)
    models = get_models(endpoint, args.key, args.timeout, tls_spoof=tls_spoof)
    models_summary = summarize_models(models.get("body"), args.model)

    probes: list[dict[str, Any]] = []
    prompt_map = dict(build_prompts(args.model, args.expected_profile))
    for name, prompt in build_prompts(args.model, args.expected_profile):
        result = call_model(
            endpoint=endpoint,
            key=args.key,
            model=args.model,
            style=args.style,
            prompt=prompt,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
            tls_spoof=tls_spoof,
        )
        result.name = name
        probes.append(asdict(result))

    consistency_analysis: dict[str, Any] = {}
    if args.samples > 1:
        sample_responses: list[str] = []
        one_word_prompt = prompt_map["one_word_probe"]
        for i in range(args.samples):
            r = call_model(
                endpoint=endpoint,
                key=args.key,
                model=args.model,
                style=args.style,
                prompt=one_word_prompt,
                timeout=args.timeout,
                max_tokens=args.max_tokens,
                tls_spoof=tls_spoof,
            )
            r.name = f"one_word_probe_sample_{i + 1}"
            sample_responses.append(r.response.strip())
            probes.append(asdict(r))
        counts = Counter(sample_responses)
        most_common, most_common_count = counts.most_common(1)[0]
        consistency_analysis = {
            "samples": args.samples,
            "most_common": most_common,
            "consistency_score": most_common_count / args.samples,
            "all_responses": sample_responses,
        }

    report = {
        "endpoint": endpoint,
        "claimed_model": args.model,
        "api_style": args.style,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "models": models,
        "models_summary": models_summary,
        "probes": probes,
        "consistency_analysis": consistency_analysis,
    }

    signals = detect_smoke_signals(report)
    report["smoke_signals"] = signals

    print_report(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON report written to {args.output}")
    if args.report:
        markdown = render_markdown_report(report, signals)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"Markdown forensic report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
