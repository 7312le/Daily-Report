#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「AI 原生 HR 一号位」成长日报。

流程：
1. 读取 .github/prompt/system.md 与 data/profile.md 作为 system prompt；
2. 按日期决定当日模块：Agent 每周 3 次、HRD 实战每日、周六深度作业、隔周高管英语；
3. 调用 Claude / Anthropic 兼容 API（如 DeepSeek）生成严格 JSON；
4. 用 templates/report.html 渲染为 HTML，写入 reports/YYYY-MM-DD.html；
5. 同时写入 reports/YYYY-MM-DD.json，供 notify.py 生成推送摘要；
6. 重建 docs/index.html 中的历史日报列表（保留页面其余部分）。

日报五个模块：组织与人才、Agent 体系化、经营与影响、HRD 实战、英语精进。
行业聚焦：工业 AI、非标自动化（蓝沃 AI / 智能装备事业线）。
只依赖标准库 + anthropic，保持轻量。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

# ---------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = ROOT / ".github" / "prompt" / "system.md"
PROFILE_PATH = ROOT / "data" / "profile.md"
TEMPLATE_PATH = ROOT / "templates" / "report.html"
REPORTS_DIR = ROOT / "reports"
INDEX_PATH = ROOT / "docs" / "index.html"
VIDEOS_PATH = ROOT / "data" / "videos.json"

# 北京时间固定为 UTC+8，使用固定偏移避免依赖 tzdata
BEIJING = timezone(timedelta(hours=8))
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-5"
# Sonnet 5 使用新分词器，同样文本约多 30% token，故留足冗余
MAX_TOKENS = 12000
MAX_ATTEMPTS = 3
RECENT_LOOKBACK = 7

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
INDEX_MARKER_RE = re.compile(
    r"<!-- REPORT_LIST_START -->.*?<!-- REPORT_LIST_END -->", re.DOTALL
)

# agent 为可选模块：非周一 / 三 / 五且非每月 1 日时为 null
REQUIRED_TOP_KEYS = (
    "date",
    "stage",
    "hrbp_coe",
    "management",
    "hrd",
    "english",
    "preview",
)

# 三条主线的阶段标识：L1–L5 / A1–A5 / M1–M4 或 M5a / M5b
STAGE_PATTERNS = {
    "hr": re.compile(r"L[1-5]", re.IGNORECASE),
    "agent": re.compile(r"A[1-5]", re.IGNORECASE),
    "mgmt": re.compile(r"M[1-5][ab]?", re.IGNORECASE),
}

ALLOWED_HRBP_TYPES = {"HRBP", "COE", "OD", "TD"}
ALLOWED_HRD_MODES = {"light", "deep"}
ALLOWED_HRD_TRACKS = ("影响力", "人才发展", "变革沟通", "用工风险", "领导力")
ALLOWED_ENGLISH_MODES = {"职场英语", "高管英语"}

# 周一到周日：HRD 实战卡的 track；周六为深度作业，track 按周轮换
WEEKDAY_HRD_TRACKS = ("影响力", "人才发展", "变革沟通", "用工风险", "领导力", "", "领导力")
AGENT_WEEKDAYS = {0, 2, 4}  # 周一、周三、周五
EXEC_ENGLISH_WEEKDAY = 2  # 隔周周三为高管英语

# 年度 HR 日历：月份 -> (节点, 说明)
HR_CALENDAR = {
    1: ("年度人力规划落地", "年度考核、调薪方案、组织与编制冻结"),
    2: ("关键人才保留与开工招聘", "春节前后留人、返岗与补员"),
    3: ("调薪与晋升", "Q1 组织复盘、晋升与调薪落地"),
    4: ("人才盘点与继任规划", "盘点会、干部校准、继任梯队"),
    5: ("校招与管培生", "雇主品牌、校招与培养计划"),
    6: ("半年复盘与干部轮岗", "半年经营复盘、干部轮岗"),
    7: ("半年考核与预算中期调整", "绩效校准、人工成本中期预算"),
    8: ("校招入职与培训体系", "新员工带教、培训体系搭建"),
    9: ("组织诊断与 Q3 复盘", "组织诊断、明年规划预热"),
    10: ("明年战略解码与预算启动", "战略解码、人力预算启动"),
    11: ("明年人力规划与预算", "组织设计与编制、人工成本预算、关键岗位继任"),
    12: ("年终奖与年度考核", "年终奖、年度考核、组织调整"),
}

# 模型常用这些词表示「本期没有」，统一视作空值
EMPTY_PLACEHOLDERS = {"", "无", "暂无", "没有", "不适用", "none", "n/a", "-", "—"}

VIDEO_PLATFORM_ICONS = {
    "B站": "📺",
    "小红书": "📕",
    "微信视频号": "🎬",
}

DEFAULT_VIDEO_TASK = "跟读视频中的 5 个表达，录音一遍后回听，标出 2 处连读不清的地方。"


# ---------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------
def esc(value: object) -> str:
    """HTML 转义，同时用于文本与属性。"""
    return html.escape(str(value if value is not None else ""), quote=True)


def one_line(value: object) -> str:
    """把任意值压成单行文本，避免换行破坏排版。"""
    return " ".join(str(value if value is not None else "").split())


def optional_text(value: object) -> str:
    """可空缺字段：把「无 / N/A」之类的占位词统一清空。"""
    text = one_line(value)
    return "" if text.strip().lower() in EMPTY_PLACEHOLDERS else text


def clip(value: object, limit: int = 80) -> str:
    """压成单行并截断。"""
    text = one_line(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def hrd_plan(now: datetime) -> dict:
    """按星期决定 HRD 实战卡的模式与 track；周六为深度作业。"""
    weekday = now.weekday()
    if weekday == 5:
        # 周六：五个 track 按 ISO 周数轮换，避免连续重复
        track = ALLOWED_HRD_TRACKS[now.isocalendar().week % len(ALLOWED_HRD_TRACKS)]
        return {"mode": "deep", "track": track}
    return {"mode": "light", "track": WEEKDAY_HRD_TRACKS[weekday]}


def include_agent(now: datetime) -> bool:
    """Agent 线每周 3 次；每月 1 日强制生成（用于月度里程碑）。"""
    return now.weekday() in AGENT_WEEKDAYS or now.day == 1


def english_mode(now: datetime) -> str:
    """隔周周三切换为高管英语。"""
    if now.weekday() == EXEC_ENGLISH_WEEKDAY and now.isocalendar().week % 2 == 1:
        return "高管英语"
    return "职场英语"


def calendar_node(now: datetime) -> dict:
    """返回当前月份对应的年度 HR 节奏节点。"""
    node, detail = HR_CALENDAR.get(now.month, ("年度常规节奏", "按季度推进"))
    return {"month": f"{now.month} 月", "node": node, "detail": detail}


def read_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[warn] 环境变量 {name}={raw!r} 不是整数，使用默认值 {default}", file=sys.stderr)
        return default


# 环境变量允许覆盖 token 上限
MAX_TOKENS = read_env_int("ANTHROPIC_MAX_TOKENS", MAX_TOKENS)


# ---------------------------------------------------------------
# 读取输入
# ---------------------------------------------------------------
def read_text_file(path: Path, label: str) -> str:
    if not path.exists():
        raise FileNotFoundError(f"{label} 不存在：{path}")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"读取 {label} 失败：{exc}") from exc
    if not content.strip():
        raise ValueError(f"{label} 内容为空：{path}")
    return content


def load_profile() -> str:
    """读取组织画像；缺失时只告警，不阻断日报生成。"""
    if not PROFILE_PATH.exists():
        print(f"[warn] 组织画像不存在：{PROFILE_PATH}，将使用行业通用情境", file=sys.stderr)
        return ""
    try:
        return PROFILE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[warn] 读取组织画像失败：{exc}", file=sys.stderr)
        return ""


def load_recent_topics(limit: int = RECENT_LOOKBACK) -> list[str]:
    """读取最近几期 JSON，提取主题，供 Prompt 用于去重。

    兼容旧版四维度结构（skills / wealth / cognition）。
    """
    entries: list[str] = []
    json_files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]

    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] 跳过无法读取的历史日报 {path.name}：{exc}", file=sys.stderr)
            continue

        hrbp = data.get("hrbp_coe") or {}
        agent = data.get("agent") or {}
        management = data.get("management") or {}
        hrd = data.get("hrd") or {}
        skills = data.get("skills") or {}
        wealth = data.get("wealth") or {}
        cognition = data.get("cognition") or {}

        parts = [
            str(item).strip()
            for item in (
                data.get("date"),
                hrbp.get("topic") or skills.get("topic"),
                agent.get("topic") or cognition.get("model"),
                management.get("topic") or wealth.get("topic"),
                hrd.get("topic"),
            )
            if item
        ]
        if parts:
            entries.append(" · ".join(parts))

    return entries


def load_video_pool() -> list[dict]:
    """读取人工核验过的视频池；只保留带 http(s) 链接与标题的条目。"""
    if not VIDEOS_PATH.exists():
        print(f"[warn] 视频池不存在：{VIDEOS_PATH}，今日不注入短视频", file=sys.stderr)
        return []

    try:
        payload = json.loads(VIDEOS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] 视频池读取失败：{exc}", file=sys.stderr)
        return []

    items = payload.get("videos") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        print("[warn] 视频池格式错误：videos 必须是数组", file=sys.stderr)
        return []

    pool: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = one_line(item.get("url"))
        title = one_line(item.get("title"))
        if not url.lower().startswith(("http://", "https://")) or not title:
            continue
        pool.append(item)
    return pool


def pick_daily_video(date_str: str) -> dict:
    """按日期在视频池中轮换，保证每天拿到一条可直接打开的视频链接。"""
    pool = load_video_pool()
    if not pool:
        return {}

    try:
        ordinal = datetime.strptime(date_str, "%Y-%m-%d").toordinal()
    except ValueError:
        ordinal = 0

    entry = pool[ordinal % len(pool)]
    video = {
        "platform": one_line(entry.get("platform")),
        "title": one_line(entry.get("title")),
        "url": one_line(entry.get("url")),
        "duration": one_line(entry.get("duration")),
        "level": one_line(entry.get("level")),
        "task": one_line(entry.get("task")) or DEFAULT_VIDEO_TASK,
    }
    print(f"[info] 今日英语视频：{video['title']}（{video['url']}）", file=sys.stderr)
    return video


def build_user_prompt(
    date_str: str,
    weekday: str,
    recent_topics: list[str],
    hrd: dict,
    agent_today: bool,
    english_today: str,
    calendar: dict,
    is_first_of_month: bool,
    is_quarter_first_week: bool,
) -> str:
    recent_block = (
        "\n".join(f"- {topic}" for topic in recent_topics)
        if recent_topics
        else "- 暂无（首次生成）"
    )

    if hrd["mode"] == "deep":
        output_rule = (
            "今天是周六深度作业日：hrd.deliverable 必须给出一个真实可交付物"
            "（一页纸备忘录、利益地图、继任图、盘点会主持稿等），hrbp_coe.output_task 可空。"
        )
    else:
        output_rule = (
            "今天不是深度作业日：hrbp_coe.output_task 若有合适输出任务则填写，否则填空字符串。"
        )

    if is_first_of_month:
        milestone_rule = "今天是每月 1 日：agent.milestone 必须填写本月小型 Agent 原型的可验收标准。"
    else:
        milestone_rule = "今天不是每月 1 日：若无安排，agent.milestone 填空字符串。"

    if is_quarter_first_week:
        influence_rule = (
            "今天是季度首月第一周：management.influence_goal 必须填写一个基于数据的经营分析会建议。"
        )
    else:
        influence_rule = "今天不在季度首周：若无安排，management.influence_goal 填空字符串。"

    if agent_today:
        agent_rule = "今天需要生成 agent 模块；如不是每月 1 日，milestone 填空字符串。"
    else:
        agent_rule = "今天不生成 agent 模块，顶层 agent 字段必须为 null，不要输出该对象。"

    if hrd["mode"] == "deep":
        hrd_rule = (
            f"hrd.mode 必须为 deep，track 必须为「{hrd['track']}」；"
            "deliverable 必须非空且可直接使用；action 也要给出今晚的准备工作。"
        )
    else:
        hrd_rule = (
            f"hrd.mode 必须为 light，track 必须为「{hrd['track']}」；"
            "action 必须具体；deliverable 填空字符串。"
        )

    calendar_month = calendar["month"]
    calendar_focus = calendar["node"]
    calendar_detail = calendar["detail"]

    return f"""请生成 {date_str}（{weekday}）的 AI 原生 HR 一号位成长日报。

服务对象：上海精智实业股份有限公司 HR 负责人，深度参与蓝沃 AI（工业 AI）与智能装备事业线（非标自动化）。

今天的年度 HR 节奏：{calendar_month} · {calendar_focus}（{calendar_detail}）。内容优先贴合这个节点。

近期 7 天已出现的主题（必须全部避开，选取不同子主题）：
{recent_block}

硬性要求：
1. 严格按 system prompt 中的 JSON 结构输出，字段齐全，不要增删字段。
2. 只输出一个 JSON 对象，不要 Markdown 代码块，不要解释文字。
3. 除英语学习素材外，全部使用简体中文；总字数不超过 1800 字。
4. 每个知识点必须带「今晚动作」，具体、可在 30 分钟内完成。
5. hrbp_coe.type 只能在 HRBP / COE / OD / TD 中选择；type 是当天的专业镜头，scenario 必须是同一个业务问题，不要为了换视角而换场景。
6. hrbp_coe.partner_action 每天必填，且必须写清：对哪个业务负责人（岗位角色，不写姓名）、在什么场合、用哪句业务语言、提出什么具体请求或判断、盯哪个指标。禁止「加强沟通」「提升意识」这类空话。
7. 场景、案例、人才判断优先落到蓝沃 AI 与智能装备事业线；行业信息只围绕工业 AI 与非标自动化。
8. 每天至少给出一个判断型结论（谁该上、谁该等、岗位要不要设、现在做还是等），不要只罗列概念。
9. {output_rule}
10. {milestone_rule}
11. {influence_rule}
12. {agent_rule}
13. {hrd_rule}
14. english.mode 必须为「{english_today}」；高管英语时围绕 board update、bad news、pushback、executive summary、resource ask。
15. english.video 各字段一律填空字符串：短视频由系统按日期从已验证的视频库注入，禁止输出任何搜索页或自行编造链接。
16. date 字段必须为 "{date_str}"；stage.hr 填 L1–L5，stage.agent 填 A1–A5，stage.mgmt 填 M1–M4 或 M5a/M5b。
"""


# ---------------------------------------------------------------
# Claude 调用与解析
# ---------------------------------------------------------------
def extract_json(text: str) -> dict:
    """从模型输出中提取 JSON，兼容 ```json 代码块与前后说明文字。"""
    if not text or not text.strip():
        raise ValueError("模型返回为空")

    cleaned = text.strip()
    cleaned = FENCE_RE.sub("", cleaned).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型输出中未找到完整的 JSON 对象")

    payload = cleaned[start : end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("JSON 根节点必须是对象")
    return data


def _require_block(data: dict, key: str) -> dict:
    block = data.get(key)
    if not isinstance(block, dict):
        raise ValueError(f"字段 {key} 必须是对象")
    return block


def _require_text(block: dict, key: str, prefix: str) -> str:
    value = one_line(block.get(key))
    if not value:
        raise ValueError(f"{prefix}.{key} 不能为空")
    return value


def validate_report(data: dict, expected: dict | None = None) -> None:
    """校验并规范化核心字段，避免把残缺内容渲染成页面。

    expected 用于校验当日轮转：hrd_mode / hrd_track / agent / english_mode。
    """
    missing = [key for key in REQUIRED_TOP_KEYS if key not in data]
    if missing:
        raise ValueError(f"缺少顶层字段：{', '.join(missing)}")

    date_str = one_line(data.get("date"))
    if not DATE_RE.match(date_str):
        raise ValueError(f"date 格式错误：{date_str}（应为 YYYY-MM-DD）")

    if not one_line(data.get("preview")):
        raise ValueError("字段 preview 不能为空")

    # --- stage ---
    stage = _require_block(data, "stage")
    for key, pattern in STAGE_PATTERNS.items():
        value = one_line(stage.get(key)).upper()
        match = pattern.search(value)
        if not match:
            raise ValueError(
                f"stage.{key} 格式错误：{value!r}"
                "（hr 应为 L1–L5，agent 应为 A1–A5，mgmt 应为 M1–M4 或 M5a/M5b）"
            )
        matched = match.group(0).upper()
        # 统一展示为 M5a / M5b
        if key == "mgmt" and matched[-1] in ("A", "B"):
            matched = matched[:-1] + matched[-1].lower()
        stage[key] = matched

    # --- 组织与人才 ---
    hrbp = _require_block(data, "hrbp_coe")
    for field in ("topic", "type", "concept", "scenario", "partner_action", "action"):
        hrbp[field] = _require_text(hrbp, field, "hrbp_coe")
    hrbp["type"] = hrbp["type"].upper()
    if hrbp["type"] not in ALLOWED_HRBP_TYPES:
        raise ValueError(f"hrbp_coe.type 非法：{hrbp['type']}（应为 HRBP / COE / OD / TD）")
    if len(hrbp["partner_action"]) < 20:
        raise ValueError(
            "hrbp_coe.partner_action 过于笼统：需写清业务负责人、场合、"
            "业务语言、具体请求与跟踪指标"
        )
    hrbp["output_task"] = optional_text(hrbp.get("output_task"))

    questions = hrbp.get("questions")
    if isinstance(questions, str):
        questions = [questions]
    if not isinstance(questions, list):
        raise ValueError("hrbp_coe.questions 必须是非空字符串数组")

    normalized: list[str] = []
    for question in questions:
        if isinstance(question, dict):
            question = next((value for value in question.values() if one_line(value)), "")
        text = one_line(question)
        if text:
            normalized.append(text)
    if not normalized:
        raise ValueError("hrbp_coe.questions 必须是非空字符串数组")
    hrbp["questions"] = normalized

    # --- Agent（可选，按星期轮转） ---
    agent_value = data.get("agent")
    if expected and expected.get("agent") and agent_value is None:
        raise ValueError("今天需要生成 agent 模块，但返回了 null")
    if expected and not expected.get("agent") and agent_value is not None:
        raise ValueError("今天不应生成 agent 模块，agent 必须为 null")

    if agent_value is None:
        data["agent"] = None
    else:
        agent = _require_block(data, "agent")
        for field in ("topic", "concept", "experiment", "action"):
            agent[field] = _require_text(agent, field, "agent")
        agent["milestone"] = optional_text(agent.get("milestone"))

    # --- 经营与影响 ---
    management = _require_block(data, "management")
    for field in ("topic", "concept", "case", "action"):
        management[field] = _require_text(management, field, "management")
    management["influence_goal"] = optional_text(management.get("influence_goal"))

    # --- HRD 实战 ---
    hrd = _require_block(data, "hrd")
    hrd["mode"] = one_line(hrd.get("mode")).lower()
    if hrd["mode"] not in ALLOWED_HRD_MODES:
        raise ValueError(f"hrd.mode 非法：{hrd['mode']!r}（应为 light / deep）")
    hrd["track"] = one_line(hrd.get("track"))
    if hrd["track"] not in ALLOWED_HRD_TRACKS:
        raise ValueError(
            f"hrd.track 非法：{hrd['track']!r}（应为 {' / '.join(ALLOWED_HRD_TRACKS)}）"
        )
    for field in ("topic", "concept", "scenario", "action"):
        hrd[field] = _require_text(hrd, field, "hrd")
    hrd["deliverable"] = optional_text(hrd.get("deliverable"))
    if hrd["mode"] == "deep" and not hrd["deliverable"]:
        raise ValueError("hrd.mode=deep 时 deliverable 不能为空")

    if expected:
        if expected.get("hrd_mode") and hrd["mode"] != expected["hrd_mode"]:
            raise ValueError(f"hrd.mode 应为 {expected['hrd_mode']}，实际为 {hrd['mode']}")
        if expected.get("hrd_track") and hrd["track"] != expected["hrd_track"]:
            raise ValueError(f"hrd.track 应为 {expected['hrd_track']}，实际为 {hrd['track']}")

    # --- 英语 ---
    english = data.get("english")
    if not isinstance(english, dict):
        raise ValueError("english 必须是对象")
    for field in ("words", "phrase", "ai_talk"):
        english[field] = _require_text(english, field, "english")
    english["mode"] = one_line(english.get("mode")) or "职场英语"
    if english["mode"] not in ALLOWED_ENGLISH_MODES:
        raise ValueError(f"english.mode 非法：{english['mode']!r}（应为 职场英语 / 高管英语）")
    if expected and expected.get("english_mode") and english["mode"] != expected["english_mode"]:
        raise ValueError(
            f"english.mode 应为 {expected['english_mode']}，实际为 {english['mode']}"
        )

    video = english.get("video")
    if video is None:
        video = {}
    if not isinstance(video, dict):
        raise ValueError("english.video 必须是对象")
    for field in ("platform", "title", "url", "duration", "level", "task"):
        video[field] = optional_text(video.get(field))
    if video["url"]:
        if not video["url"].lower().startswith(("http://", "https://")):
            raise ValueError("english.video.url 必须以 http:// 或 https:// 开头")
        if not video["platform"]:
            raise ValueError("english.video.platform 不能为空")
        if not video["title"]:
            raise ValueError("english.video.title 不能为空")
    english["video"] = video


def call_claude(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_prompt: str,
    expected: dict | None = None,
) -> dict:
    """调用模型，解析失败时携带错误信息重试。"""
    attempt_prompt = user_prompt
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"[info] 调用 Claude（第 {attempt}/{MAX_ATTEMPTS} 次，model={MODEL}）…", file=sys.stderr)
            # claude-sonnet-5 不接受非默认 temperature/top_p/top_k，传了会 400；
            # 且省略 thinking 时默认开启 adaptive thinking，会挤占 max_tokens。
            # 日报是结构化内容生成，显式关闭 thinking 以获得稳定可预期的输出预算。
            message = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                thinking={"type": "disabled"},
                system=system_prompt,
                messages=[{"role": "user", "content": attempt_prompt}],
            )

            if getattr(message, "stop_reason", None) == "max_tokens":
                raise ValueError("输出被 max_tokens 截断，请缩短内容或提高 ANTHROPIC_MAX_TOKENS")

            text = "".join(
                getattr(block, "text", "")
                for block in message.content
                if getattr(block, "type", "") == "text"
            )
            data = extract_json(text)
            validate_report(data, expected)
            return data

        except anthropic.AuthenticationError:
            # 密钥错误重试无意义，直接抛出
            raise
        except (ValueError, anthropic.APIError) as exc:
            last_error = exc
            print(f"[warn] 第 {attempt} 次生成失败：{exc}", file=sys.stderr)
            if attempt >= MAX_ATTEMPTS:
                break
            attempt_prompt = (
                user_prompt
                + "\n\n上一次输出存在问题："
                + str(exc)[:500]
                + "\n请重新生成。务必只输出合法 JSON，不要代码块或多余文字，"
                + "并确保所有必填字段齐全、字段值为单行字符串。"
            )
            time.sleep(min(2 ** attempt, 10))

    raise RuntimeError(f"Claude 连续 {MAX_ATTEMPTS} 次未能生成合法日报：{last_error}")


# ---------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------
def card_head(icon: str, kicker: str, title: str, badge: str = "") -> str:
    badge_html = f'<span class="card-badge">{esc(badge)}</span>' if badge else ""
    return (
        '<header class="card-head">'
        f'<span class="card-icon" aria-hidden="true">{icon}</span>'
        "<div>"
        f'<p class="card-kicker">{esc(kicker)}</p>'
        f'<h2 class="card-title">{esc(title)}{badge_html}</h2>'
        "</div>"
        "</header>"
    )


def render_field(label: str, value: object) -> str:
    content = one_line(value)
    if not content:
        return ""
    return (
        '<div class="field">'
        f'<p class="field-label">{esc(label)}</p>'
        f'<p class="field-text">{esc(content)}</p>'
        "</div>"
    )


def render_action(value: object) -> str:
    content = one_line(value)
    if not content:
        return ""
    return (
        '<div class="action-box">'
        '<span class="action-label">今晚动作</span>'
        f"<p>{esc(content)}</p>"
        "</div>"
    )


def render_questions(questions: list[str]) -> str:
    items = "".join(f"<li>{esc(one_line(q))}</li>" for q in questions if one_line(q))
    if not items:
        return ""
    return (
        '<div class="field">'
        '<p class="field-label">诊断问题</p>'
        f'<ul class="question-list">{items}</ul>'
        "</div>"
    )


def render_task_box(label: str, value: object, css_class: str) -> str:
    content = one_line(value)
    if not content:
        return ""
    return (
        f'<div class="{css_class}">'
        f'<span class="task-label">{esc(label)}</span>'
        f"<p>{esc(content)}</p>"
        "</div>"
    )


def render_hrbp_coe(hrbp: dict, stage: str) -> str:
    title = one_line(hrbp.get("topic")) or "组织与人才"
    body = "".join(
        [
            render_field("概念 / 工具", hrbp.get("concept")),
            render_field("业务场景", hrbp.get("scenario")),
            render_questions(hrbp.get("questions") or []),
        ]
    )
    return (
        '<article class="card card-hr">'
        + card_head("🧭", f"01 · 组织与人才 · {stage}", title, badge=one_line(hrbp.get("type")))
        + body
        + render_task_box("业务伙伴动作", hrbp.get("partner_action"), "partner-box")
        + render_action(hrbp.get("action"))
        + render_task_box("本周输出任务", hrbp.get("output_task"), "output-box")
        + "</article>"
    )


def render_agent(agent: dict, stage: str) -> str:
    title = one_line(agent.get("topic")) or "Agent 体系化"
    body = "".join(
        [
            render_field("核心概念", agent.get("concept")),
            render_field("动手实验", agent.get("experiment")),
        ]
    )
    return (
        '<article class="card card-agent">'
        + card_head("🤖", f"02 · Agent 体系化 · {stage}", title)
        + body
        + render_action(agent.get("action"))
        + render_task_box("月度里程碑", agent.get("milestone"), "milestone-box")
        + "</article>"
    )


def render_management(management: dict, stage: str, wide: bool = False) -> str:
    title = one_line(management.get("topic")) or "经营与影响"
    css = "card card-mgmt card-wide" if wide else "card card-mgmt"
    body = "".join(
        [
            render_field("概念 / 指标", management.get("concept")),
            render_field("真实案例", management.get("case")),
        ]
    )
    return (
        f'<article class="{css}">'
        + card_head("📊", f"03 · 经营与影响 · {stage}", title)
        + body
        + render_action(management.get("action"))
        + render_task_box("季度影响力目标", management.get("influence_goal"), "influence-box")
        + "</article>"
    )


def render_hrd(hrd: dict) -> str:
    title = one_line(hrd.get("topic")) or "HRD 实战"
    mode = one_line(hrd.get("mode"))
    track = one_line(hrd.get("track"))
    badge = f"{track} · 深度作业" if mode == "deep" else track
    body = "".join(
        [
            render_field("框架 / 判断标准", hrd.get("concept")),
            render_field("公司情境", hrd.get("scenario")),
        ]
    )
    deliverable = (
        render_task_box("周六深度交付物", hrd.get("deliverable"), "deliverable-box")
        if mode == "deep"
        else ""
    )
    return (
        '<article class="card card-hrd">'
        + card_head("🎯", "04 · HRD 实战", title, badge=badge)
        + body
        + render_action(hrd.get("action"))
        + deliverable
        + "</article>"
    )


def render_video(video: dict) -> str:
    url = one_line(video.get("url"))
    if not url:
        return ""
    platform = one_line(video.get("platform"))
    icon = VIDEO_PLATFORM_ICONS.get(platform, "🎬")
    meta_parts = [part for part in (platform, one_line(video.get("duration")), one_line(video.get("level"))) if part]
    meta = " · ".join(meta_parts)
    task = one_line(video.get("task"))
    task_html = f'<span class="video-task">任务：{esc(task)}</span>' if task else ""

    return (
        f'<a class="video-card" href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
        f'<span class="video-icon" aria-hidden="true">{icon}</span>'
        '<span class="video-main">'
        f'<span class="video-platform">{esc(meta)}</span>'
        f'<span class="video-title">{esc(one_line(video.get("title")))}</span>'
        f"{task_html}"
        "</span>"
        '<span class="video-arrow" aria-hidden="true">↗</span>'
        "</a>"
    )


def render_english(english: dict) -> str:
    blocks = [
        '<section class="english-block">'
        "<h3>金山词霸 · 词汇任务</h3>"
        f'<p class="field-text">{esc(one_line(english.get("words")))}</p>'
        "</section>",
        '<section class="english-block">'
        "<h3>高频短语 / 句型</h3>"
        f'<p class="field-text">{esc(one_line(english.get("phrase")))}</p>'
        "</section>",
        '<section class="english-block">'
        "<h3>AI Talk · 情景对话</h3>"
        f'<p class="field-text">{esc(one_line(english.get("ai_talk")))}</p>'
        "</section>",
    ]

    video_html = render_video(english.get("video") or {})
    if video_html:
        blocks.append(
            '<section class="english-block english-video">'
            "<h3>今日英语短视频</h3>"
            f"{video_html}"
            "</section>"
        )

    mode = one_line(english.get("mode"))
    title = f"英语精进 · {mode}" if mode else "英语精进 · 每天 20 分钟"
    return (
        '<article class="card card-english">'
        + card_head("🌍", "05 · 英语精进", title)
        + '<div class="english-grid">'
        + "".join(blocks)
        + "</div>"
        + "</article>"
    )


def render_cards(data: dict) -> str:
    stage = data.get("stage") or {}
    agent = data.get("agent")
    has_agent = isinstance(agent, dict) and bool(agent)

    cards = [render_hrbp_coe(data.get("hrbp_coe") or {}, one_line(stage.get("hr")))]
    if has_agent:
        cards.append(render_agent(agent, one_line(stage.get("agent"))))
    cards.append(
        render_management(
            data.get("management") or {},
            one_line(stage.get("mgmt")),
            wide=not has_agent,
        )
    )
    cards.append(render_hrd(data.get("hrd") or {}))
    cards.append(render_english(data.get("english") or {}))
    return "\n".join(cards)


def build_summary(data: dict) -> str:
    """用于 meta / 首页列表的一句话摘要。"""
    parts: list[str] = []
    hrbp = data.get("hrbp_coe") or {}
    agent = data.get("agent") or {}
    management = data.get("management") or {}
    hrd = data.get("hrd") or {}
    english = data.get("english") or {}

    if one_line(hrbp.get("topic")):
        topic = one_line(hrbp.get("topic"))
        partner = clip(hrbp.get("partner_action"), 26)
        suffix = f"｜伙伴动作：{partner}" if partner else ""
        parts.append(f"组织与人才：{topic}{suffix}")
    if one_line(agent.get("topic")):
        parts.append(f"Agent：{one_line(agent.get('topic'))}")
    if one_line(management.get("topic")):
        parts.append(f"经营：{one_line(management.get('topic'))}")
    if one_line(hrd.get("topic")):
        parts.append(f"HRD：{clip(hrd.get('topic'), 24)}")
    if one_line(english.get("phrase")):
        parts.append(f"英语：{clip(english.get('phrase'), 24)}")

    return " ｜ ".join(parts)


def render_report(
    data: dict,
    template: str,
    date_str: str,
    weekday: str,
    now: datetime,
    calendar: dict,
) -> str:
    stage = data.get("stage") or {}
    hrbp = data.get("hrbp_coe") or {}
    agent = data.get("agent") or {}
    management = data.get("management") or {}
    hrd = data.get("hrd") or {}

    stage_text = " ｜ ".join(
        [
            f"HR · {one_line(stage.get('hr'))}",
            f"Agent · {one_line(stage.get('agent'))}",
            f"经营 · {one_line(stage.get('mgmt'))}",
            f"HRD · {one_line(hrd.get('track'))}",
        ]
    )
    focus = " ｜ ".join(
        part
        for part in (
            one_line(hrbp.get("topic")),
            one_line(agent.get("topic")),
            one_line(management.get("topic")),
            one_line(hrd.get("topic")),
        )
        if part
    )
    calendar_text = f"年度节奏：{calendar.get('month', '')} · {calendar.get('node', '')}"

    replacements = {
        "PAGE_TITLE": esc(f"Shaun Daily Report · {date_str}"),
        "DATE": esc(date_str),
        "WEEKDAY": esc(weekday),
        "STAGES": esc(stage_text),
        "CALENDAR": esc(calendar_text),
        "FOCUS": esc(focus),
        "SUMMARY_ATTR": esc(build_summary(data)),
        "DATA": render_cards(data),
        "PREVIEW": esc(one_line(data.get("preview"))),
        "GENERATED_AT": esc(now.strftime("%Y-%m-%d %H:%M") + "（北京时间）"),
    }

    output = template
    for key, value in replacements.items():
        output = output.replace("{{" + key + "}}", value)

    if "{{" in output:
        leftovers = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", output)))
        print(f"[warn] 模板中存在未替换占位符：{leftovers}", file=sys.stderr)

    return output


# ---------------------------------------------------------------
# 首页更新
# ---------------------------------------------------------------
def read_meta(html_text: str, name: str) -> str:
    pattern = re.compile(
        rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"', re.IGNORECASE
    )
    match = pattern.search(html_text)
    return html.unescape(match.group(1)).strip() if match else ""


def render_index_item(date_str: str, summary: str) -> str:
    try:
        weekday = WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except ValueError:
        weekday = ""

    weekday_html = (
        f'<span class="item-weekday">{esc(weekday)}</span>' if weekday else ""
    )
    summary = summary or "点击查看当日日报"

    return (
        "      <li>\n"
        f'        <a class="report-item" href="reports/{esc(date_str)}.html">\n'
        '          <span class="item-main">\n'
        '            <span class="item-date-row">\n'
        f'              <span class="item-date">{esc(date_str)}</span>\n'
        f"              {weekday_html}\n"
        "            </span>\n"
        f'            <span class="item-summary">{esc(summary)}</span>\n'
        "          </span>\n"
        '          <span class="item-arrow" aria-hidden="true">→</span>\n'
        "        </a>\n"
        "      </li>"
    )


def update_index() -> None:
    """只替换 docs/index.html 中标记区间，保留页面其余设计。"""
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"首页不存在：{INDEX_PATH}")

    items: list[str] = []
    for report_path in sorted(REPORTS_DIR.glob("*.html"), reverse=True):
        date_str = report_path.stem
        if not DATE_RE.match(date_str):
            continue

        try:
            report_html = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[warn] 无法读取 {report_path.name}：{exc}", file=sys.stderr)
            continue

        summary = read_meta(report_html, "report-summary")
        if not summary:
            summary = read_meta(report_html, "description")
        items.append(render_index_item(date_str, summary))

    if items:
        block = "\n".join(items)
    else:
        block = '      <li class="empty-state">还没有日报，等待第一次自动生成。</li>'

    index_html = INDEX_PATH.read_text(encoding="utf-8")
    if not INDEX_MARKER_RE.search(index_html):
        raise RuntimeError(
            "docs/index.html 缺少 <!-- REPORT_LIST_START --> / "
            "<!-- REPORT_LIST_END --> 标记"
        )

    new_block = (
        "<!-- REPORT_LIST_START -->\n"
        f"{block}\n"
        "      <!-- REPORT_LIST_END -->"
    )
    updated = INDEX_MARKER_RE.sub(new_block, index_html, count=1)
    INDEX_PATH.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------
def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[error] 缺少环境变量 ANTHROPIC_API_KEY", file=sys.stderr)
        return 1

    try:
        system_prompt = read_text_file(SYSTEM_PROMPT_PATH, "System Prompt")
        template = read_text_file(TEMPLATE_PATH, "日报模板")
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    profile = load_profile()
    if profile:
        system_prompt = (
            system_prompt.rstrip()
            + "\n\n## 十一、组织画像（本次生成必须结合）\n\n"
            + profile
        )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(BEIJING)
    date_str = now.strftime("%Y-%m-%d")
    weekday = WEEKDAYS[now.weekday()]

    plan = hrd_plan(now)
    agent_today = include_agent(now)
    english_today = english_mode(now)
    calendar = calendar_node(now)
    is_first_of_month = now.day == 1
    is_quarter_first_week = now.month in (1, 4, 7, 10) and now.day <= 7

    print(
        f"[info] 今日安排：HRD {plan['mode']}/{plan['track']}，"
        f"Agent {'生成' if agent_today else '跳过'}，英语 {english_today}，"
        f"节奏 {calendar['month']}·{calendar['node']}",
        file=sys.stderr,
    )

    recent_topics = load_recent_topics()
    if recent_topics:
        print(f"[info] 已加载 {len(recent_topics)} 条近期主题用于去重", file=sys.stderr)

    user_prompt = build_user_prompt(
        date_str,
        weekday,
        recent_topics,
        plan,
        agent_today,
        english_today,
        calendar,
        is_first_of_month,
        is_quarter_first_week,
    )

    try:
        # 支持 Anthropic 兼容端点（如 DeepSeek https://api.deepseek.com/anthropic）；
        # 未设置 ANTHROPIC_BASE_URL 时走官方 api.anthropic.com
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "").strip() or None,
            timeout=120.0,
            max_retries=2,
        )
        expected = {
            "hrd_mode": plan["mode"],
            "hrd_track": plan["track"],
            "agent": agent_today,
            "english_mode": english_today,
        }
        data = call_claude(client, system_prompt, user_prompt, expected)
    except anthropic.AuthenticationError as exc:
        # 输出 API 返回的原文（如 invalid x-api-key），便于区分密钥错误与其他鉴权问题
        print(f"[error] ANTHROPIC_API_KEY 无效或已过期：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，保证退出码正确
        print(f"[error] 生成日报失败：{exc}", file=sys.stderr)
        return 1

    # 以脚本日期为准，避免模型返回错误日期
    if one_line(data.get("date")) != date_str:
        print(
            f"[warn] 模型返回日期 {data.get('date')!r} 与目标 {date_str} 不一致，已覆盖",
            file=sys.stderr,
        )
        data["date"] = date_str

    # 短视频不走模型生成：从人工核验的视频池按日期轮换，确保是可直接打开的播放页
    daily_video = pick_daily_video(date_str)
    if daily_video:
        data["english"]["video"] = daily_video

    try:
        page = render_report(data, template, date_str, weekday, now, calendar)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 渲染 HTML 失败：{exc}", file=sys.stderr)
        return 1

    report_html_path = REPORTS_DIR / f"{date_str}.html"
    report_json_path = REPORTS_DIR / f"{date_str}.json"

    try:
        report_html_path.write_text(page, encoding="utf-8")
        report_json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[error] 写入日报文件失败：{exc}", file=sys.stderr)
        return 1

    # 首页更新失败不应阻断日报提交，下一次运行会自动修复列表
    try:
        update_index()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 更新首页失败（日报仍会提交）：{exc}", file=sys.stderr)

    print(f"[ok] 日报 HTML：{report_html_path}")
    print(f"[ok] 日报 JSON：{report_json_path}")
    print(f"[ok] 首页：{INDEX_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
