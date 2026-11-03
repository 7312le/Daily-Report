# v024 · 日报补上每日「业务伙伴动作」

- 版本：v024
- 时间：2026-11-03（北京时间）
- 类型：结构升级（方案二）

## 背景

v023 把 01 线扩成 HRBP / COE / OD / TD 四视角，但 `type` 只在 prompt 里要求「尽量与前一天不同」，没有确定性轮转，也不注入前一天类型；模型自然偏向有工具可写的 COE / OD / TD。结果 HRBP 变成「四天轮到一次的标签」，业务伙伴站位被稀释。

方案二：不把 HRBP 做成第四天轮值，而是把「业务伙伴动作」设为每天必填字段；`type` 只决定当天用哪个专业镜头看同一个业务问题。

## 变更

| 文件 | 变更 |
| --- | --- |
| `.github/prompt/system.md` | 新增 `partner_action` 字段说明与 JSON 字段；明确 HRBP 是站位、COE / OD / TD 是镜头；01 线结构改为「概念 → 业务场景 → 诊断问题 → 业务伙伴动作 → 今晚动作」 |
| `scripts/generate.py` | 校验 `partner_action` 必填且 ≥20 字（拦截空话）；user prompt 新增硬性要求；01 卡渲染「业务伙伴动作」框；首页摘要带伙伴动作 |
| `scripts/notify.py` | 推送摘要新增「伙伴动作」一行 |
| `templates/report.html` | 新增 `.partner-box` 样式（蓝色左边框 + 浅底） |
| `README.md` | 01 模块说明改为「四视角镜头 + 每日业务伙伴动作」 |

## 字段要求

`hrbp_coe.partner_action` 每天必填，必须写清五件事：

1. 对哪个业务负责人（岗位角色，不写姓名）
2. 在什么场合
3. 用哪句业务语言（把人和事翻译成产能 / 交付 / 毛利 / 签约风险）
4. 提出什么具体请求或判断
5. 之后盯哪个指标

禁止「加强沟通」「提升意识」「推动协同」这类空话，校验层拦截少于 20 字的输入。

## 验证

- `py_compile` 通过（generate.py / notify.py）。
- 缺少 `partner_action` 的旧产物被拒绝。
- 含完整伙伴动作的样例通过校验。
- 「加强沟通」被拒绝。
- 渲染：4 张卡片、`partner-box` 存在、无未替换占位符。
- 首页摘要与推送摘要均包含「伙伴动作」。

## 回滚

恢复 v023 的 `.github/prompt/system.md`、`scripts/generate.py`、`scripts/notify.py`、`templates/report.html`、`README.md` 即可；无数据迁移。
