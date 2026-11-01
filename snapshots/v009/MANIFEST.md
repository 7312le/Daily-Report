# 版本快照 v009

- 时间：2026-10-31
- 类型：提供商切换（Anthropic → DeepSeek 兼容端点）
- 基线：v008

## 背景

用户确认使用的是 **DeepSeek 的 API Key**，而非 Anthropic Console 的 Key。此前 workflow 调的是官方 `api.anthropic.com`，因此即使 Key 填写完整也会返回 `401 invalid x-api-key`。

DeepSeek 提供 Anthropic 兼容端点，`anthropic` SDK 只需改 `base_url` 即可调用，无需更换 SDK。

## 相对 v008 的变更

| 文件 | 变更 |
| --- | --- |
| `.github/workflows/generate.yml` | 生成步骤新增 `ANTHROPIC_BASE_URL`（默认 `https://api.deepseek.com/anthropic`，可由仓库变量覆盖）；`ANTHROPIC_MODEL` 默认值改为 `deepseek-flash`（可由仓库变量覆盖） |
| `scripts/generate.py` | `anthropic.Anthropic(...)` 显式传入 `base_url`（读 `ANTHROPIC_BASE_URL`，为空则走官方默认） |

## 兼容性依据（DeepSeek 官方文档）

- Base URL：`https://api.deepseek.com/anthropic`；支持 `x-api-key` 鉴权。
- `thinking` 支持 `enabled` / `disabled`；`temperature` 在非思考模式生效。
- 模型名 `deepseek-flash`（1M 上下文，最大输出 384K）；Claude 名称会被自动映射（`claude-*` → `deepseek-flash` / `deepseek-v4-pro`）。
- 当前代码使用 `thinking={"type": "disabled"}`、`max_tokens=12000`，均在支持范围内。

## 验证记录

- 待重新运行 `generate.yml` 验证（Key 需为 DeepSeek 平台 Key，Secret 名称仍为 `ANTHROPIC_API_KEY`）。
