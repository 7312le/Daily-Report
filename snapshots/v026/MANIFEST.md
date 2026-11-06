# v026 · 日报改为「知识地图 + 案例驱动」的 HR 系统课

- 版本：v026
- 时间：2026-11-06（北京时间）
- 类型：结构升级

## 背景

用户反馈：日报强行结合蓝沃 AI / 智能装备事业部和 11 月编制节点，内容生硬；举例多处失真；不想再按年度日历滚动；HRBP / OD / TD 仍是小白，希望多吸收新知识。

根因：年度日历把 11 月固定成「明年人力规划与预算」，提示词要求所有模块「优先贴合该节点」；`scenario` 被要求必须落到两个事业部，而画像中真实事实不足，模型只能编造或写成通用案例；7 天去重只比较标题，无法保证知识面。

## 新日报结构

| 模块 | 频率 | 内容 |
| --- | --- | --- |
| 01 今日一课 | 每日 | 一句话定义、为什么重要、3 步怎么用、2–3 条常见误区、2–4 条术语翻译、今晚 10 分钟动作 |
| 02 案例照镜子 | 每日 | 公开案例：问题、做法、公开结果、能否迁移、一句判断 |
| 03 小测一题 | 每日 | 情境题 + 3 个选项 + 答案 + 解析 |
| 04 Agent 小卡 | 周三 | 与当天课程独立，30 分钟内可搭出原型 |
| 05 英语 5 分钟 | 每日 | 词汇、句型、AI Talk；隔周周三高管英语 |
| 06 周六落地日 | 周六 | 一个可选落地问题，不要求公司信息 |

## 学习机制

- `data/curriculum.json`：12 个领域 × 5 课 = 60 课，按顺序学习；一轮学完后从最久没学的课轮换。
- `data/curriculum_state.json`：记录每课学习日期、7 天 / 30 天复习标记、案例使用日期。日报成功落盘后才推进进度。
- `data/cases.json`：18 个公开案例，按领域标签匹配，30 天内不重复。
- 同一天重复触发直接跳过，避免重复消耗课程进度。

## 变更

| 文件 | 变更 |
| --- | --- |
| `.github/prompt/system.md` | 重写为 HR 系统课教练；零基础友好；只输出 lesson / quiz / english / agent / landing / review |
| `scripts/generate.py` | 新增课程表、案例库、学习状态加载；课程选择、案例匹配、7/30 天复习、幂等跳过；新增课程 / 案例 / 小测 / Agent / 落地日渲染；移除年度日历、组织画像注入、旧五模块 |
| `scripts/notify.py` | 推送摘要改为新结构 |
| `templates/report.html` | 新卡片体系与复习提示；响应式布局 |
| `data/curriculum.json` | 新增 60 课知识地图 |
| `data/cases.json` | 新增 18 个公开案例库 |
| `data/curriculum_state.json` | 新增学习进度（首次生成后写入） |
| `data/profile.md` | 标注为参考文件，不再注入 |
| `.github/workflows/generate.yml` | 提交时带上 `data/curriculum_state.json` |
| `README.md` | 结构、学习机制、加课 / 加案例说明 |
| `docs/index.html` | lede、chips、meta description 同步 |

## 字段

模型输出：`lesson`（one_liner / why / how_to_use / misconceptions / glossary / action）、`quiz`（question / options / answer / explanation）、`english`（mode / words / phrase / ai_talk）、`agent`、`landing`、`review`。

脚本注入：`date`、`lesson.domain`、`lesson.title`、`lesson.lesson_no`、`case`、`progress`、`preview`、`english.video`。

## 验证

- `py_compile` 通过。
- 离线 fixture：课程选择 d01-l01、案例 alibaba-commissar、下一课 d01-l02、7 天复习命中、状态推进、渲染无占位符、推送摘要 2560 字节。
- 真实调用生成 `reports/2026-11-06.json/html`：第 1/60 课、案例阿里政委、无公司绑定、无年度日历；该日为周五，按新频率不含 Agent 卡。
- 浏览器预览无控制台错误；同一天重复运行正确跳过。

## 回滚

恢复 v025 的 `.github/prompt/system.md`、`scripts/generate.py`、`scripts/notify.py`、`templates/report.html`、`README.md`、`docs/index.html`、`.github/workflows/generate.yml`；删除 `data/curriculum.json`、`data/cases.json`、`data/curriculum_state.json`；`data/profile.md` 恢复为运行时注入。
