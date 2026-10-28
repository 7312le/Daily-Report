# 版本快照 v002

- 时间：2026-10-27
- 类型：功能落地（个人成长日报系统）
- 说明：完整实现 Claude API 生成、GitHub Actions 定时任务、GitHub Pages 部署、企业微信通知。

## 变更清单

| 文件 | 说明 |
| --- | --- |
| `.github/workflows/generate.yml` | UTC 23:00 定时 + 手动触发；生成、通知、提交推送 |
| `.github/workflows/pages.yml` | push main 后部署 Pages；合并 `docs/` 与 `reports/` |
| `.github/prompt/system.md` | 四维度日报 Agent 人设、JSON Schema、禁止事项 |
| `scripts/generate.py` | Claude 调用、JSON 清洗校验、HTML/JSON 落盘、首页更新 |
| `scripts/notify.py` | 企业微信 markdown 摘要、截断、重试、dry-run |
| `templates/report.html` | 响应式日报模板（四卡片 + 明日预览） |
| `docs/index.html` | 历史归档首页，含列表标记区间 |
| `requirements.txt` | `anthropic`、`requests` |
| `.gitignore` | 忽略密钥、缓存、构建产物 |
| `reports/.gitkeep` | 占位，保持目录结构 |
| `CLAUDE.md` / `dialog.md` | 项目纪律与对话归档（本阶段更新） |

## 验证记录

- Pyright：语法通过，仅提示本地未安装 `anthropic` / `requests`。
- YAML：两份 workflow 均可被 YAML 1.2 解析器解析。
- Pyodide 冒烟测试：4 张卡片渲染、占位符全部替换、首页列表更新、企业微信消息 1743 字节、UTF-8 安全截断、Pages 地址推导。
- html-validate：渲染样例与首页均无错误。

## 已知说明

- cron 使用需求中明确指定的 `0 23 * * *`，即北京时间 07:00；如需 07:30 改为 `30 23 * * *`。
- 通知步骤位于 push 之前，Pages 链接约有 1–2 分钟部署延迟；通知失败不会阻断提交。

## 回滚方式

将本目录内容复制回项目根目录，即可恢复到本版本。后续版本不会覆盖本目录。
