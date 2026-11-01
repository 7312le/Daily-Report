# 版本快照 v007

- 时间：2026-10-31
- 类型：线上故障修复（首次运行 generate.yml 失败）
- 基线：v006

## 背景

手动触发 `generate.yml`（run 41182064833），步骤「Generate daily report」退出码 1：

```
TypeError: Messages.create() got an unexpected keyword argument 'temperature'
```

## 根因

1. `requirements.txt` 中 `anthropic>=0.40.0` 未锁上限，Actions runner 安装了 `anthropic-2.3.0`。
2. 目标模型 `claude-sonnet-5` 不再接受非默认 `temperature` / `top_p` / `top_k`（传值直接 400），且 2.x SDK 的 `Messages.create()` 已移除 `temperature` 关键字参数。
3. 附带风险：Sonnet 5 省略 `thinking` 时默认开启 adaptive thinking，thinking token 与正文共享 `max_tokens`；且新分词器同文本约多 30% token，原 8000 上限可能触发「输出被 max_tokens 截断」。

## 相对 v006 的变更

| 文件 | 变更 |
| --- | --- |
| `scripts/generate.py` | 删除 `temperature=0.6`；显式传 `thinking={"type": "disabled"}`（结构化 JSON 生成，无需 thinking，避免挤占输出预算）；`MAX_TOKENS` 默认 8000 → 12000（新分词器留冗余，仍可被 `ANTHROPIC_MAX_TOKENS` 覆盖） |
| `requirements.txt` | `anthropic>=0.40.0` → `anthropic>=2.3.0,<3.0.0`，锁定已测试的大版本，防止再次被无预告升级 |

## 验证记录

- 本地无 Python 运行时，无法做语法/类型检查；改动仅删除一个参数、新增一个合法参数、调整常量，风险面小。
- 真实验证以重新运行 `generate.yml` 为准（生成 HTML → 更新首页 → 提交 → Pages 部署 → 企业微信推送）。

## 回滚方式

将 `snapshots/v006/` 对应内容复制回项目根目录即可恢复。注意：回滚到 v006 会重新引入 `temperature` 报错，仅用于对照排查。
