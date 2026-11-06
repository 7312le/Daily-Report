#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「每日 HR 系统课」日报。

流程：
1. 读取 data/curriculum.json 选今日课程，data/cases.json 选今日案例；
2. 读取 data/curriculum_state.json 记录已学课程、复习计划与案例使用；
3. 调用 Claude / Anthropic 兼容 API 生成课程、测验、英语、Agent 卡；
4. 用 templates/report.html 渲染 HTML，写入 reports/YYYY-MM-DD.html 与 .json；
5. 成功后更新课程进度，并重建 docs/index.html 中的历史列表。

日报结构：今日一课、案例照镜子、小测一题、Agent 小卡（每周三）、
英语 5 分钟、周六落地日（可选）。不再按年度日历滚动，也不强制结合公司情境。
只依赖标准库 + anthropic，保持轻量。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

# ---------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = ROOT / ".github" / "prompt" / "system.md"
TEMPLATE_PATH = ROOT / "templates" / "report.html"
REPORTS_DIR = ROOT / "reports"
INDEX_PATH = ROOT / "docs" / "index.html"
VIDEOS_PATH = ROOT / "data" / "videos.json"
CURRICULUM_PATH = ROOT / "data" / "curriculum.json"
CASES_PATH = ROOT / "data" / "cases.json"
STATE_PATH = ROOT / "data" / "curriculum_state.json"

# 北京时间固定为 UTC+8，使用固定偏移避免依赖 tzdata
BEIJING = timezone(timedelta(hours=8))
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-5"
MAX_TOKENS = 12000
MAX_ATTEMPTS = 3
RECENT_LOOKBACK = 7

# Agent 卡每周三出现；周六为可选落地日
AGENT_WEEKDAYS = {2}
LANDING_WEEKDAY = 5
EXEC_ENGLISH_WEEKDAY = 2  # 隔周周三为高管英语

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
INDEX_MARKER_RE = re.compile(
    r"<!-- REPORT_LIST_START -->.*?<!-- REPORT_LIST_END -->", re.DOTALL
)

# 模型必须输出的字段；date / case / progress / preview 由脚本注入
MODEL_KEYS = ("lesson", "quiz", "english", "agent", "landing", "review")

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


def parse_date(value: object) -> date_cls | None:
    text = one_line(value)
    if not DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def read_json_file(path: Path, label: str):
    if not path.exists():
        raise FileNotFoundError(f"{label} 不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取 {label} 失败：{exc}") from exc


def write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------
# 课程表 / 案例库 / 学习状态
# ---------------------------------------------------------------
def load_curriculum() -> dict:
    payload = read_json_file(CURRICULUM_PATH, "课程表")
    domains = payload.get("domains") if isinstance(payload, dict) else None
    if not isinstance(domains, list) or not domains:
        raise ValueError("课程表格式错误：domains 必须是非空数组")

    seen: set[str] = set()
    for domain in domains:
        if not isinstance(domain, dict):
            raise ValueError("课程表格式错误：domain 必须是对象")
        if not one_line(domain.get("id")) or not one_line(domain.get("name")):
            raise ValueError("课程表格式错误：domain 缺少 id 或 name")
        lessons = domain.get("lessons")
        if not isinstance(lessons, list) or not lessons:
            raise ValueError(f"课程表格式错误：{domain.get('name')} 没有 lesson")
        for lesson in lessons:
            if not isinstance(lesson, dict):
                raise ValueError("课程表格式错误：lesson 必须是对象")
            lesson_id = one_line(lesson.get("id"))
            title = one_line(lesson.get("title"))
            if not lesson_id or not title:
                raise ValueError("课程表格式错误：lesson 缺少 id 或 title")
            if lesson_id in seen:
                raise ValueError(f"课程表格式错误：lesson id 重复 {lesson_id}")
            seen.add(lesson_id)
    return payload


def flatten_lessons(curriculum: dict) -> list[dict]:
    domains = curriculum["domains"]
    total = sum(len(domain["lessons"]) for domain in domains)
    result: list[dict] = []
    number = 0
    for domain_index, domain in enumerate(domains, 1):
        for lesson in domain["lessons"]:
            number += 1
            result.append(
                {
                    "id": one_line(lesson["id"]),
                    "title": one_line(lesson["title"]),
                    "domain_id": one_line(domain["id"]),
                    "domain": one_line(domain["name"]),
                    "domain_index": domain_index,
                    "domain_total": len(domains),
                    "lesson_no": number,
                    "total": total,
                }
            )
    return result


def load_cases() -> list[dict]:
    payload = read_json_file(CASES_PATH, "案例库")
    items = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise ValueError("案例库格式错误：cases 必须是非空数组")

    required = ("id", "title", "problem", "practice", "result", "transfer", "judgement")
    cases: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("案例库格式错误：case 必须是对象")
        if not all(one_line(item.get(key)) for key in required):
            raise ValueError(f"案例库格式错误：{item.get('title') or item.get('id')} 字段不全")
        case_id = one_line(item["id"])
        if case_id in seen:
            raise ValueError(f"案例库格式错误：case id 重复 {case_id}")
        seen.add(case_id)
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError(f"案例库格式错误：{case_id} 的 tags 必须是数组")
        cases.append(
            {
                "id": case_id,
                "title": one_line(item["title"]),
                "tags": [one_line(tag) for tag in tags if one_line(tag)],
                "source": one_line(item.get("source")),
                "problem": one_line(item["problem"]),
                "practice": one_line(item["practice"]),
                "result": one_line(item["result"]),
                "transfer": one_line(item["transfer"]),
                "judgement": one_line(item["judgement"]),
            }
        )
    return cases


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "updated_at": None, "lessons": {}, "cases": {}}
    payload = read_json_file(STATE_PATH, "学习状态")
    if not isinstance(payload, dict):
        raise ValueError("学习状态格式错误：根节点必须是对象")
    if not isinstance(payload.get("lessons"), dict):
        payload["lessons"] = {}
    if not isinstance(payload.get("cases"), dict):
        payload["cases"] = {}
    return payload


def save_state(state: dict) -> None:
    write_json_file(STATE_PATH, state)


def find_review(lessons: list[dict], state: dict, today: date_cls) -> dict | None:
    """找出到期的复习课：7 天复习优先，其次 30 天复习。"""
    infos = state.get("lessons") or {}
    candidates: list[tuple[int, date_cls, int, dict, str, str]] = []
    for lesson in lessons:
        info = infos.get(lesson["id"]) or {}
        covered_on = parse_date(info.get("covered_on"))
        if not covered_on:
            continue
        days = (today - covered_on).days
        if days < 7:
            continue
        if not info.get("review_7_on"):
            candidates.append(
                (0, covered_on, lesson["lesson_no"], lesson, "7 天复习", "review_7_on")
            )
        elif days >= 30 and not info.get("review_30_on"):
            candidates.append(
                (1, covered_on, lesson["lesson_no"], lesson, "30 天复习", "review_30_on")
            )
    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, covered_on, _, lesson, kind, flag = candidates[0]
    return {
        "lesson": lesson,
        "kind": kind,
        "flag": flag,
        "covered_on": covered_on.isoformat(),
    }


def choose_new_lesson(lessons: list[dict], state: dict) -> dict:
    """按课程表顺序取第一门没学过的课；全部学完后取最久没学的一门。"""
    covered = state.get("lessons") or {}
    for lesson in lessons:
        if lesson["id"] not in covered:
            return lesson

    def covered_on(lesson: dict) -> date_cls:
        info = covered.get(lesson["id"]) or {}
        return parse_date(info.get("covered_on")) or date_cls.min

    return min(lessons, key=covered_on)


def select_lesson(lessons: list[dict], state: dict, today: date_cls) -> dict:
    review = find_review(lessons, state, today)
    lesson = choose_new_lesson(lessons, state)
    return {"lesson": lesson, "review": review}


def select_case(cases: list[dict], domain_id: str, state: dict, today: date_cls) -> dict:
    """优先选同领域、30 天没用过的案例；没有就退到任意新案例，最后选最久没用的。"""
    used = state.get("cases") or {}

    def last_used(case: dict) -> date_cls:
        info = used.get(case["id"]) or {}
        return parse_date(info.get("used_on")) or date_cls.min

    def days_since(case: dict) -> int:
        last = last_used(case)
        return 9999 if last == date_cls.min else (today - last).days

    matching = [case for case in cases if domain_id in case["tags"]]
    fresh_matching = [case for case in matching if days_since(case) >= 30]
    if fresh_matching:
        return fresh_matching[0]

    fresh_all = [case for case in cases if days_since(case) >= 30]
    if fresh_all:
        return fresh_all[0]

    pool = matching or cases
    return min(pool, key=lambda case: (last_used(case), case["title"]))


def peek_next_lesson(lessons: list[dict], state: dict, current_id: str) -> dict | None:
    """为「明日预览」找下一门课：优先当前课之后第一门没学过的。"""
    covered = state.get("lessons") or {}
    start = next(
        (index for index, lesson in enumerate(lessons) if lesson["id"] == current_id), -1
    )
    for offset in range(1, len(lessons) + 1):
        lesson = lessons[(start + offset) % len(lessons)]
        if lesson["id"] == current_id:
            continue
        if lesson["id"] not in covered:
            return lesson

    candidates = [lesson for lesson in lessons if lesson["id"] != current_id]
    if not candidates:
        return None

    def covered_on(lesson: dict) -> date_cls:
        info = covered.get(lesson["id"]) or {}
        return parse_date(info.get("covered_on")) or date_cls.min

    return min(candidates, key=covered_on)


def recent_lesson_titles(lessons: list[dict], state: dict, limit: int = RECENT_LOOKBACK) -> list[str]:
    infos = state.get("lessons") or {}
    entries: list[tuple[date_cls, int, str]] = []
    for lesson in lessons:
        info = infos.get(lesson["id"]) or {}
        covered_on = parse_date(info.get("covered_on"))
        if covered_on:
            entries.append((covered_on, lesson["lesson_no"], lesson["title"]))
    entries.sort(reverse=True)
    return [f"第 {number} 课《{title}》" for _, number, title in entries[:limit]]


def update_state(state: dict, selection: dict, case: dict, today: date_cls) -> None:
    today_str = today.isoformat()
    lesson_info = state.setdefault("lessons", {}).setdefault(
        selection["lesson"]["id"], {}
    )
    lesson_info["covered_on"] = today_str
    lesson_info["review_7_on"] = None
    lesson_info["review_30_on"] = None

    review = selection.get("review")
    if review:
        review_info = state["lessons"].setdefault(review["lesson"]["id"], {})
        review_info[review["flag"]] = today_str

    state.setdefault("cases", {})[case["id"]] = {"used_on": today_str}
    state["updated_at"] = today_str


# ---------------------------------------------------------------
# 英语视频池
# ---------------------------------------------------------------
def load_video_pool() -> list[dict]:
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


def english_mode(now: datetime) -> str:
    if now.weekday() == EXEC_ENGLISH_WEEKDAY and now.isocalendar().week % 2 == 1:
        return "高管英语"
    return "职场英语"


# ---------------------------------------------------------------
# 生成 Prompt
# ---------------------------------------------------------------
def build_user_prompt(
    date_str: str,
    weekday: str,
    selection: dict,
    recent_titles: list[str],
    case: dict,
    agent_today: bool,
    landing_today: bool,
    english_today: str,
) -> str:
    lesson = selection["lesson"]
    review = selection.get("review")

    recent_block = (
        "\n".join(f"- {title}" for title in recent_titles)
        if recent_titles
        else "- 暂无（第一次学习）"
    )

    if agent_today:
        agent_rule = (
            "今天生成 Agent 卡：agent 必须是对象，字段 title、goal、steps、action；"
            "与今日 HR 主题无关也可以，选一个能在 30 分钟内用现成平台搭出最小原型的 HR 场景工具。"
        )
    else:
        agent_rule = "今天不生成 Agent 卡：agent 必须为 null。"

    if landing_today:
        landing_rule = (
            "今天是周六落地日：landing 必须是对象，字段 question、note；"
            "只给一个可选的落地问题，不要求学员提供公司信息，不编造公司情境。"
        )
    else:
        landing_rule = "今天不是周六：landing 必须为 null。"

    if review:
        review_rule = (
            "今天需要复习：review 必须是对象，lesson_title 原样写"
            f"「{review['lesson']['title']}」，prompt 写一个 3 分钟内可回答的回忆问题。"
        )
    else:
        review_rule = "今天没有复习任务：review 必须为 null。"

    return f"""请生成 {date_str}（{weekday}）的「每日 HR 系统课」。

今日课程：
- 领域：{lesson['domain']}（第 {lesson['domain_index']}/{lesson['domain_total']} 个领域）
- 课号：第 {lesson['lesson_no']}/{lesson['total']} 课
- 课名：{lesson['title']}

近期已学（用于避免重复举例，不要换题）：
{recent_block}

今日案例（系统提供，原文使用；不要改写，不要输出 case 字段）：
标题：{case['title']}
它解决什么问题：{case['problem']}
它怎么做：{case['practice']}
公开信息能看到的结果：{case['result']}
能不能迁移：{case['transfer']}
一句判断：{case['judgement']}

今日安排：
- Agent 卡：{'生成' if agent_today else '不生成'}
- 周六落地日：{'是' if landing_today else '否'}
- 英语：{english_today}
- 复习：{f"第 {review['lesson']['lesson_no']} 课《{review['lesson']['title']}》（{review['kind']}）" if review else "无"}

硬性要求：
1. 只输出一个 JSON 对象，严格按 system prompt 的结构，不要 Markdown 代码块，不要解释文字。
2. lesson 只讲「{lesson['title']}」这一个概念；读者是 HR 零基础，术语第一次出现必须用人话解释。
3. lesson.how_to_use 必须 3 条；misconceptions 必须 2–3 条；glossary 必须 2–4 条。
4. quiz 围绕今日课程出 1 道情境题，正好 3 个选项；answer 只能是 A/B/C；explanation 要说明另外两个为什么不对。
5. {agent_rule}
6. {landing_rule}
7. {review_rule}
8. 不要提任何具体公司作为学员所在公司；不要编造公司数据、客户名、人名。需要举例时写「假设一家……（虚构示例）」。
9. 所有字符串单行；除英语素材外全部简体中文；总字数不超过 1200 字。
"""


# ---------------------------------------------------------------
# Claude 调用与解析
# ---------------------------------------------------------------
def extract_json(text: str) -> dict:
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


def _require_list(block: dict, key: str, prefix: str, min_len: int) -> list[str]:
    value = block.get(key)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{prefix}.{key} 必须是字符串数组")
    items = [one_line(item) for item in value if one_line(item)]
    if len(items) < min_len:
        raise ValueError(f"{prefix}.{key} 至少需要 {min_len} 条")
    block[key] = items
    return items


def _normalize_glossary(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("lesson.glossary 必须是对象数组")
    items: list[dict] = []
    for entry in value:
        term = ""
        plain = ""
        if isinstance(entry, dict):
            term = one_line(entry.get("term"))
            plain = one_line(
                entry.get("plain")
                or entry.get("meaning")
                or entry.get("explanation")
                or entry.get("definition")
            )
        elif isinstance(entry, str):
            text = one_line(entry)
            for separator in ("：", ":", "=", "—"):
                if separator in text:
                    term, plain = text.split(separator, 1)
                    break
            term, plain = one_line(term), one_line(plain)
        if term and plain:
            items.append({"term": term, "plain": plain})
    if len(items) < 2:
        raise ValueError("lesson.glossary 至少需要 2 条「术语 + 人话解释」")
    return items[:6]


def _normalize_options(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("quiz.options 必须是字符串数组")
    options: list[str] = []
    for item in value:
        text = one_line(item)
        text = re.sub(r"^[A-Ca-c]\s*[\.、:：)\]]\s*", "", text)
        text = re.sub(r"^[A-Ca-c]\s+", "", text)
        if text:
            options.append(text)
    if len(options) != 3:
        raise ValueError("quiz.options 必须正好 3 条")
    return options


def _normalize_answer(value: object) -> str:
    text = one_line(value).upper()
    match = re.search(r"[ABC]", text)
    if not match:
        raise ValueError("quiz.answer 必须是 A/B/C")
    return match.group(0)


def validate_report(data: dict, expected: dict) -> None:
    """校验模型输出；domain / title / 课号由脚本覆盖，避免模型跑题。"""
    missing = [key for key in MODEL_KEYS if key not in data]
    if missing:
        raise ValueError(f"缺少顶层字段：{', '.join(missing)}")

    # --- 今日一课 ---
    lesson = _require_block(data, "lesson")
    lesson["one_liner"] = _require_text(lesson, "one_liner", "lesson")
    lesson["why"] = _require_text(lesson, "why", "lesson")
    lesson["action"] = _require_text(lesson, "action", "lesson")
    _require_list(lesson, "how_to_use", "lesson", 3)
    lesson["how_to_use"] = lesson["how_to_use"][:3]
    _require_list(lesson, "misconceptions", "lesson", 2)
    lesson["misconceptions"] = lesson["misconceptions"][:3]
    lesson["glossary"] = _normalize_glossary(lesson.get("glossary"))[:4]

    lesson["domain"] = expected["lesson"]["domain"]
    lesson["title"] = expected["lesson"]["title"]
    lesson["lesson_no"] = expected["lesson"]["lesson_no"]

    # --- 小测 ---
    quiz = _require_block(data, "quiz")
    quiz["question"] = _require_text(quiz, "question", "quiz")
    quiz["options"] = _normalize_options(quiz.get("options"))
    quiz["answer"] = _normalize_answer(quiz.get("answer"))
    quiz["explanation"] = _require_text(quiz, "explanation", "quiz")

    # --- 英语 ---
    english = _require_block(data, "english")
    for field in ("words", "phrase", "ai_talk"):
        english[field] = _require_text(english, field, "english")
    english["mode"] = one_line(english.get("mode")) or expected["english_mode"]
    if english["mode"] != expected["english_mode"]:
        raise ValueError(
            f"english.mode 应为 {expected['english_mode']}，实际为 {english['mode']}"
        )
    english["video"] = {}

    # --- Agent 卡 ---
    if expected["agent"]:
        agent = _require_block(data, "agent")
        for field in ("title", "goal", "steps", "action"):
            agent[field] = _require_text(agent, field, "agent")
    elif data.get("agent") is not None:
        raise ValueError("今天不应生成 Agent 卡，agent 必须为 null")

    # --- 周六落地日 ---
    if expected["landing"]:
        landing = _require_block(data, "landing")
        landing["question"] = _require_text(landing, "question", "landing")
        landing["note"] = optional_text(landing.get("note"))
    elif data.get("landing") is not None:
        raise ValueError("今天不是周六，landing 必须为 null")

    # --- 复习 ---
    if expected["review"]:
        review = _require_block(data, "review")
        review["lesson_title"] = expected["review"]["lesson"]["title"]
        review["prompt"] = _require_text(review, "prompt", "review")
    elif data.get("review") is not None:
        raise ValueError("今天没有复习任务，review 必须为 null")


def call_claude(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_prompt: str,
    expected: dict,
) -> dict:
    attempt_prompt = user_prompt
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(
                f"[info] 调用 Claude（第 {attempt}/{MAX_ATTEMPTS} 次，model={MODEL}）…",
                file=sys.stderr,
            )
            # claude-sonnet-5 不接受非默认 temperature/top_p/top_k；
            # 显式关闭 thinking，避免挤占 max_tokens。
            message = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                thinking={"type": "disabled"},
                system=system_prompt,
                messages=[{"role": "user", "content": attempt_prompt}],
            )

            if getattr(message, "stop_reason", None) == "max_tokens":
                raise ValueError(
                    "输出被 max_tokens 截断，请缩短内容或提高 ANTHROPIC_MAX_TOKENS"
                )

            text = "".join(
                getattr(block, "text", "")
                for block in message.content
                if getattr(block, "type", "") == "text"
            )
            data = extract_json(text)
            validate_report(data, expected)
            return data

        except anthropic.AuthenticationError:
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
            time.sleep(min(2**attempt, 10))

    raise RuntimeError(f"Claude 连续 {MAX_ATTEMPTS} 次未能生成合法日报：{last_error}")


# ---------------------------------------------------------------
# 系统字段注入
# ---------------------------------------------------------------
def build_preview(next_lesson: dict | None, now: datetime) -> str:
    tomorrow = now + timedelta(days=1)
    if next_lesson:
        text = (
            f"明日：第 {next_lesson['lesson_no']}/{next_lesson['total']} 课"
            f"《{next_lesson['title']}》"
        )
    else:
        text = "明日：完成本轮课程，进入复习轮换"
    agent_text = "Agent 卡出现" if tomorrow.weekday() in AGENT_WEEKDAYS else "Agent 卡暂停"
    landing_text = "｜周六落地日" if tomorrow.weekday() == LANDING_WEEKDAY else ""
    return f"{text}｜{agent_text}｜英语 {english_mode(tomorrow)}{landing_text}"


def attach_system_fields(
    data: dict,
    date_str: str,
    selection: dict,
    case: dict,
    next_lesson: dict | None,
    now: datetime,
) -> None:
    lesson = data["lesson"]
    lesson["domain"] = selection["lesson"]["domain"]
    lesson["title"] = selection["lesson"]["title"]
    lesson["lesson_no"] = selection["lesson"]["lesson_no"]

    data["date"] = date_str
    data["case"] = {
        key: one_line(case.get(key))
        for key in ("title", "source", "problem", "practice", "result", "transfer", "judgement")
    }
    data["progress"] = {
        "domain": selection["lesson"]["domain"],
        "domain_index": selection["lesson"]["domain_index"],
        "domain_total": selection["lesson"]["domain_total"],
        "lesson_no": selection["lesson"]["lesson_no"],
        "total": selection["lesson"]["total"],
    }

    review = selection.get("review")
    if isinstance(data.get("review"), dict) and review:
        data["review"]["kind"] = review["kind"]
        data["review"]["lesson_no"] = review["lesson"]["lesson_no"]

    data["preview"] = build_preview(next_lesson, now)


# ---------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------
def card_head(icon: str, kicker: str, title: object, badge: str = "") -> str:
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


def render_bullets(label: str, items: list[str]) -> str:
    content = "".join(f"<li>{esc(one_line(item))}</li>" for item in items if one_line(item))
    if not content:
        return ""
    return (
        '<div class="field">'
        f'<p class="field-label">{esc(label)}</p>'
        f'<ul class="bullet-list">{content}</ul>'
        "</div>"
    )


def render_steps(label: str, items: list[str]) -> str:
    content = "".join(f"<li>{esc(one_line(item))}</li>" for item in items if one_line(item))
    if not content:
        return ""
    return (
        '<div class="field">'
        f'<p class="field-label">{esc(label)}</p>'
        f'<ol class="step-list">{content}</ol>'
        "</div>"
    )


def render_glossary(items: list[dict]) -> str:
    rows = "".join(
        '<div class="glossary-item">'
        f'<dt>{esc(one_line(item.get("term")))}</dt>'
        f'<dd>{esc(one_line(item.get("plain")))}</dd>'
        "</div>"
        for item in items
        if one_line(item.get("term")) and one_line(item.get("plain"))
    )
    if not rows:
        return ""
    return (
        '<div class="field">'
        '<p class="field-label">今日术语</p>'
        f'<dl class="glossary-list">{rows}</dl>'
        "</div>"
    )


def render_action(value: object, label: str = "今晚 10 分钟") -> str:
    content = one_line(value)
    if not content:
        return ""
    return (
        '<div class="action-box">'
        f'<span class="action-label">{esc(label)}</span>'
        f"<p>{esc(content)}</p>"
        "</div>"
    )


def render_review(review: dict) -> str:
    title = one_line(review.get("lesson_title"))
    prompt = one_line(review.get("prompt"))
    if not title or not prompt:
        return ""
    kind = one_line(review.get("kind")) or "复习"
    number = one_line(review.get("lesson_no"))
    number_text = f"第 {number} 课" if number else "复习课"
    return (
        '<div class="review-strip">'
        f'<span class="review-label">🔁 {esc(kind)}</span>'
        f'<p><strong>{esc(number_text)}《{esc(title)}》</strong>：{esc(prompt)}</p>'
        "</div>"
    )


def render_lesson(lesson: dict, progress: dict, review: dict | None) -> str:
    badge = f"第 {lesson.get('lesson_no')}/{progress.get('total')} 课"
    kicker = f"01 · 今日一课 · {lesson.get('domain')}"
    body = "".join(
        [
            render_field("一句话定义", lesson.get("one_liner")),
            render_field("为什么重要", lesson.get("why")),
            render_steps("怎么用", lesson.get("how_to_use") or []),
            render_bullets("常见误区", lesson.get("misconceptions") or []),
            render_glossary(lesson.get("glossary") or []),
        ]
    )
    review_html = render_review(review) if review else ""
    return (
        '<article class="card card-lesson">'
        + card_head("📚", kicker, lesson.get("title"), badge)
        + review_html
        + body
        + render_action(lesson.get("action"), "今晚 10 分钟")
        + "</article>"
    )


def render_case(case: dict) -> str:
    body = "".join(
        [
            render_field("它解决什么问题", case.get("problem")),
            render_field("它怎么做", case.get("practice")),
            render_field("公开信息能看到的结果", case.get("result")),
            render_field("能不能迁移", case.get("transfer")),
        ]
    )
    judgement = one_line(case.get("judgement"))
    judgement_html = (
        '<div class="judgement-box">'
        '<span class="judgement-label">一句判断</span>'
        f"<p>{esc(judgement)}</p>"
        "</div>"
        if judgement
        else ""
    )
    source = one_line(case.get("source"))
    source_html = f'<p class="source-note">来源：{esc(source)}</p>' if source else ""
    return (
        '<article class="card card-case">'
        + card_head("🪞", "02 · 案例照镜子", case.get("title"), "公开案例")
        + body
        + judgement_html
        + source_html
        + "</article>"
    )


def render_quiz(quiz: dict, wide: bool) -> str:
    options = quiz.get("options") or []
    letters = "ABC"
    option_html = "".join(
        '<li><span class="option-letter">'
        f"{letters[index]}</span><span>{esc(one_line(option))}</span></li>"
        for index, option in enumerate(options)
        if index < len(letters)
    )
    body = render_field("情境", quiz.get("question")) + (
        '<div class="field">'
        '<p class="field-label">选项</p>'
        f'<ul class="quiz-options">{option_html}</ul>'
        "</div>"
    )
    answer = one_line(quiz.get("answer"))
    explanation = one_line(quiz.get("explanation"))
    answer_html = (
        '<div class="answer-box">'
        f'<span class="answer-label">答案 {esc(answer)}</span>'
        f"<p>{esc(explanation)}</p>"
        "</div>"
    )
    css = "card card-quiz card-wide" if wide else "card card-quiz"
    return (
        f'<article class="{css}">'
        + card_head("✅", "03 · 小测一题", "检验一下", "3 选 1")
        + body
        + answer_html
        + "</article>"
    )


def render_agent(agent: dict) -> str:
    body = "".join(
        [
            render_field("目标", agent.get("goal")),
            render_field("怎么做", agent.get("steps")),
        ]
    )
    return (
        '<article class="card card-agent">'
        + card_head("🤖", "04 · Agent 小卡", agent.get("title"))
        + body
        + render_action(agent.get("action"), "今晚 20 分钟")
        + "</article>"
    )


def render_video(video: dict) -> str:
    url = one_line(video.get("url"))
    if not url:
        return ""
    platform = one_line(video.get("platform"))
    icon = VIDEO_PLATFORM_ICONS.get(platform, "🎬")
    meta_parts = [
        part
        for part in (platform, one_line(video.get("duration")), one_line(video.get("level")))
        if part
    ]
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
    title = f"英语 · {mode}" if mode else "英语 5 分钟"
    return (
        '<article class="card card-english">'
        + card_head("🌍", "05 · 英语 5 分钟", title)
        + '<div class="english-grid">'
        + "".join(blocks)
        + "</div>"
        + "</article>"
    )


def render_landing(landing: dict) -> str:
    question = one_line(landing.get("question"))
    body = (
        '<div class="landing-question">'
        f"<p>{esc(question)}</p>"
        "</div>"
        if question
        else ""
    )
    return (
        '<article class="card card-landing card-wide">'
        + card_head("🧩", "06 · 周六落地日", "把这一周放回你的工作", "可选")
        + body
        + render_field("提示", landing.get("note"))
        + "</article>"
    )


def render_cards(data: dict) -> str:
    lesson = data.get("lesson") or {}
    case = data.get("case") or {}
    quiz = data.get("quiz") or {}
    agent = data.get("agent")
    has_agent = isinstance(agent, dict) and bool(agent)
    landing = data.get("landing")

    cards = [
        render_lesson(lesson, data.get("progress") or {}, data.get("review")),
        render_case(case),
        render_quiz(quiz, wide=not has_agent),
    ]
    if has_agent:
        cards.append(render_agent(agent))
    cards.append(render_english(data.get("english") or {}))
    if isinstance(landing, dict) and landing:
        cards.append(render_landing(landing))
    return "\n".join(cards)


def build_summary(data: dict) -> str:
    lesson = data.get("lesson") or {}
    case = data.get("case") or {}
    quiz = data.get("quiz") or {}
    progress = data.get("progress") or {}

    parts: list[str] = []
    if one_line(lesson.get("title")):
        parts.append(
            f"第 {lesson.get('lesson_no')}/{progress.get('total')} 课："
            f"{one_line(lesson.get('title'))}"
        )
    if one_line(case.get("title")):
        parts.append(f"案例：{one_line(case.get('title'))}")
    if one_line(quiz.get("question")):
        parts.append(f"小测：{clip(quiz.get('question'), 30)}")
    return " ｜ ".join(parts)


def render_report(data: dict, template: str, date_str: str, weekday: str, now: datetime) -> str:
    lesson = data.get("lesson") or {}
    case = data.get("case") or {}
    progress = data.get("progress") or {}
    review = data.get("review") or {}

    progress_text = (
        f"领域 {progress.get('domain_index')}/{progress.get('domain_total')}"
        f" · {progress.get('domain')}"
        f"　｜　第 {progress.get('lesson_no')}/{progress.get('total')} 课"
    )
    review_text = ""
    if isinstance(review, dict) and review:
        review_text = (
            f"🔁 今日复习：第 {review.get('lesson_no')} 课"
            f"《{review.get('lesson_title')}》"
        )
    focus = " ｜ ".join(
        part
        for part in (
            one_line(lesson.get("title")),
            f"案例：{one_line(case.get('title'))}" if one_line(case.get("title")) else "",
        )
        if part
    )

    replacements = {
        "PAGE_TITLE": esc(f"每日 HR 系统课 · {date_str}"),
        "DATE": esc(date_str),
        "WEEKDAY": esc(weekday),
        "PROGRESS": esc(progress_text),
        "REVIEW": esc(review_text),
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
    summary = summary or "点击查看当日课程"

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
        block = '      <li class="empty-state">还没有课程，等待第一次自动生成。</li>'

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
        system_prompt_path = SYSTEM_PROMPT_PATH
        if not system_prompt_path.exists():
            raise FileNotFoundError(f"System Prompt 不存在：{system_prompt_path}")
        system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
        if not system_prompt:
            raise ValueError("System Prompt 内容为空")
        if not TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"日报模板不存在：{TEMPLATE_PATH}")
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        curriculum = load_curriculum()
        lessons = flatten_lessons(curriculum)
        cases = load_cases()
        state = load_state()
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(BEIJING)
    today = now.date()
    date_str = now.strftime("%Y-%m-%d")
    weekday = WEEKDAYS[now.weekday()]

    # 同一天重复触发时保持幂等：已有日报就不重复消耗课程进度
    report_json_path = REPORTS_DIR / f"{date_str}.json"
    if report_json_path.exists():
        print(
            f"[skip] {date_str} 的日报已存在，跳过生成；"
            "如需重做，请先删除当天的 reports 文件并清理进度记录",
            file=sys.stderr,
        )
        return 0

    selection = select_lesson(lessons, state, today)
    lesson = selection["lesson"]
    review = selection.get("review")
    agent_today = now.weekday() in AGENT_WEEKDAYS
    landing_today = now.weekday() == LANDING_WEEKDAY
    english_today = english_mode(now)
    case = select_case(cases, lesson["domain_id"], state, today)
    next_lesson = peek_next_lesson(lessons, state, lesson["id"])
    recent_titles = recent_lesson_titles(lessons, state)

    review_label = (
        f"{review['kind']}·第 {review['lesson']['lesson_no']} 课" if review else "无"
    )
    print(
        f"[info] 今日课程：第 {lesson['lesson_no']}/{lesson['total']} 课"
        f"《{lesson['title']}》（{lesson['domain']}）；"
        f"案例《{case['title']}》；Agent {'生成' if agent_today else '跳过'}；"
        f"落地日 {'是' if landing_today else '否'}；复习 {review_label}；"
        f"英语 {english_today}",
        file=sys.stderr,
    )

    user_prompt = build_user_prompt(
        date_str,
        weekday,
        selection,
        recent_titles,
        case,
        agent_today,
        landing_today,
        english_today,
    )

    try:
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "").strip() or None,
            timeout=120.0,
            max_retries=2,
        )
        expected = {
            "lesson": lesson,
            "review": review,
            "agent": agent_today,
            "landing": landing_today,
            "english_mode": english_today,
        }
        data = call_claude(client, system_prompt, user_prompt, expected)
    except anthropic.AuthenticationError as exc:
        print(f"[error] ANTHROPIC_API_KEY 无效或已过期：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，保证退出码正确
        print(f"[error] 生成日报失败：{exc}", file=sys.stderr)
        return 1

    attach_system_fields(data, date_str, selection, case, next_lesson, now)
    data["english"]["video"] = pick_daily_video(date_str)

    try:
        page = render_report(data, template, date_str, weekday, now)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 渲染 HTML 失败：{exc}", file=sys.stderr)
        return 1

    report_html_path = REPORTS_DIR / f"{date_str}.html"

    try:
        report_html_path.write_text(page, encoding="utf-8")
        report_json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[error] 写入日报文件失败：{exc}", file=sys.stderr)
        return 1

    # 进度只在日报成功落盘后推进；保存失败不阻断日报，下一次会重新讲这一课
    try:
        update_state(state, selection, case, today)
        save_state(state)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 保存学习进度失败（日报仍会保留）：{exc}", file=sys.stderr)

    # 首页更新失败不应阻断日报提交，下一次运行会自动修复列表
    try:
        update_index()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 更新首页失败（日报仍会提交）：{exc}", file=sys.stderr)

    print(f"[ok] 日报 HTML：{report_html_path}")
    print(f"[ok] 日报 JSON：{report_json_path}")
    print(f"[ok] 学习进度：{STATE_PATH}")
    print(f"[ok] 首页：{INDEX_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
