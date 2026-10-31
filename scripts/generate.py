#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成个人成长日报。

流程：
1. 读取 .github/prompt/system.md 作为 system prompt；
2. 调用 Claude API 生成严格 JSON（自动清洗代码块包裹）；
3. 用 templates/report.html 渲染为 HTML，写入 reports/YYYY-MM-DD.html；
4. 同时写入 reports/YYYY-MM-DD.json，供 notify.py 生成企业微信摘要；
5. 重建 docs/index.html 中的历史日报列表（保留页面其余部分）。

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
TEMPLATE_PATH = ROOT / "templates" / "report.html"
REPORTS_DIR = ROOT / "reports"
INDEX_PATH = ROOT / "docs" / "index.html"

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

REQUIRED_TOP_KEYS = (
    "date",
    "one_line",
    "skills",
    "wealth",
    "cognition",
    "english",
    "tomorrow_preview",
)

DIMENSION_KEYS = {
    "skills": ("topic", "concept", "scenario", "diagnosis", "action"),
    "wealth": ("topic", "concept", "case", "pitfall", "source", "action"),
    "cognition": ("model", "explain", "steps", "reflection"),
}


# ---------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------
def esc(value: object) -> str:
    """HTML 转义，同时用于文本与属性。"""
    return html.escape(str(value if value is not None else ""), quote=True)


def one_line(value: object) -> str:
    """把任意值压成单行文本，避免 JSON 中的换行破坏排版。"""
    return " ".join(str(value if value is not None else "").split())


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


def load_recent_topics(limit: int = RECENT_LOOKBACK) -> list[str]:
    """读取最近几期 JSON，提取主题，供 Prompt 用于去重。"""
    entries: list[str] = []
    json_files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]

    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] 跳过无法读取的历史日报 {path.name}：{exc}", file=sys.stderr)
            continue

        skills = data.get("skills") or {}
        wealth = data.get("wealth") or {}
        cognition = data.get("cognition") or {}

        parts = [
            str(item).strip()
            for item in (
                data.get("date"),
                skills.get("topic"),
                wealth.get("topic"),
                cognition.get("model"),
            )
            if item
        ]
        if parts:
            entries.append(" · ".join(parts))

    return entries


def build_user_prompt(date_str: str, weekday: str, recent_topics: list[str]) -> str:
    recent_block = (
        "\n".join(f"- {topic}" for topic in recent_topics)
        if recent_topics
        else "- 暂无（首次生成）"
    )
    return f"""请生成 {date_str}（{weekday}）的个人成长日报。

近期 7 天已出现的主题（必须全部避开，选取不同子主题）：
{recent_block}

硬性要求：
1. 严格按 system prompt 中的 JSON 结构输出，字段齐全，不要增删字段。
2. 只输出一个 JSON 对象，不要 Markdown 代码块，不要解释文字。
3. 除英语学习素材外，全部使用简体中文。
4. 内容具体、可执行，每个「今晚动作」控制在 30 分钟内。
5. 财富板块只讲方法论，不得出现任何具体标的或买卖建议。
6. 英语部分固定包含金山词霸词汇任务、AI Talk 情景对话、语法点、听力跟读。
7. date 字段必须为 "{date_str}"。
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


def validate_report(data: dict) -> None:
    """校验核心字段，避免把残缺内容渲染成页面。"""
    missing = [key for key in REQUIRED_TOP_KEYS if key not in data]
    if missing:
        raise ValueError(f"缺少顶层字段：{', '.join(missing)}")

    for key in ("date", "one_line", "tomorrow_preview"):
        if not one_line(data.get(key)):
            raise ValueError(f"字段 {key} 不能为空")

    date_str = one_line(data.get("date"))
    if not DATE_RE.match(date_str):
        raise ValueError(f"date 格式错误：{date_str}（应为 YYYY-MM-DD）")

    for key, fields in DIMENSION_KEYS.items():
        block = data.get(key)
        if not isinstance(block, dict):
            raise ValueError(f"字段 {key} 必须是对象")
        for field in fields:
            if field == "steps":
                steps = block.get(field)
                if not isinstance(steps, list) or not any(one_line(s) for s in steps):
                    raise ValueError(f"{key}.steps 必须是非空数组")
            elif not one_line(block.get(field)):
                raise ValueError(f"{key}.{field} 不能为空")

    english = data.get("english")
    if not isinstance(english, dict):
        raise ValueError("english 必须是对象")

    vocab = english.get("vocab")
    if not isinstance(vocab, list) or not any(isinstance(item, dict) for item in vocab):
        raise ValueError("english.vocab 必须是非空对象数组")

    dialogue = english.get("dialogue")
    if not isinstance(dialogue, dict) or not isinstance(dialogue.get("lines"), list):
        raise ValueError("english.dialogue.lines 必须是数组")
    if not any(isinstance(line, dict) for line in dialogue["lines"]):
        raise ValueError("english.dialogue.lines 不能为空")

    if not isinstance(english.get("grammar"), dict):
        raise ValueError("english.grammar 必须是对象")
    if not isinstance(english.get("listening"), dict):
        raise ValueError("english.listening 必须是对象")


def call_claude(client: anthropic.Anthropic, system_prompt: str, user_prompt: str) -> dict:
    """调用 Claude，解析失败时携带错误信息重试。"""
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
            validate_report(data)
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
                + "并确保所有必填字段齐全。"
            )
            time.sleep(min(2 ** attempt, 10))

    raise RuntimeError(f"Claude 连续 {MAX_ATTEMPTS} 次未能生成合法日报：{last_error}")


# ---------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------
def card_head(icon: str, kicker: str, title: str) -> str:
    return (
        '<header class="card-head">'
        f'<span class="card-icon" aria-hidden="true">{icon}</span>'
        "<div>"
        f'<p class="card-kicker">{esc(kicker)}</p>'
        f'<h2 class="card-title">{esc(title)}</h2>'
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


def render_skills(skills: dict) -> str:
    title = one_line(skills.get("topic")) or "技能栈"
    body = "".join(
        [
            render_field("概念 / 工具", skills.get("concept")),
            render_field("业务场景", skills.get("scenario")),
            render_field("诊断问题", skills.get("diagnosis")),
        ]
    )
    return (
        '<article class="card card-skills">'
        + card_head("🧭", "01 · 技能栈", title)
        + body
        + render_action(skills.get("action"))
        + "</article>"
    )


def render_wealth(wealth: dict) -> str:
    title = one_line(wealth.get("topic")) or "财富自由"
    source = one_line(wealth.get("source"))
    body = "".join(
        [
            render_field("核心概念", wealth.get("concept")),
            render_field("案例剖析", wealth.get("case")),
            render_field("常见误区", wealth.get("pitfall")),
        ]
    )
    source_html = f'<p class="source-tag">{esc(source)}</p>' if source else ""
    return (
        '<article class="card card-wealth">'
        + card_head("💰", "02 · 财富自由", title)
        + body
        + source_html
        + render_action(wealth.get("action"))
        + "</article>"
    )


def render_cognition(cognition: dict) -> str:
    title = one_line(cognition.get("model")) or "认知"
    explain = render_field("通俗解释", cognition.get("explain"))

    steps = cognition.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    steps_html = "".join(
        f"<li>{esc(one_line(step))}</li>" for step in steps if one_line(step)
    )
    steps_block = (
        '<div class="field">'
        '<p class="field-label">应用步骤</p>'
        f'<ol class="steps">{steps_html}</ol>'
        "</div>"
        if steps_html
        else ""
    )

    reflection = one_line(cognition.get("reflection"))
    reflection_html = (
        '<div class="reflection">'
        '<span class="field-label">反思问题</span>'
        f"<p>{esc(reflection)}</p>"
        "</div>"
        if reflection
        else ""
    )

    return (
        '<article class="card card-cognition">'
        + card_head("🧠", "03 · 认知", title)
        + explain
        + steps_block
        + reflection_html
        + "</article>"
    )


def render_english(english: dict) -> str:
    # --- 词汇 ---
    vocab_items = []
    for item in english.get("vocab") or []:
        if not isinstance(item, dict):
            continue
        word = one_line(item.get("word"))
        if not word:
            continue
        phonetic = one_line(item.get("phonetic"))
        meaning = one_line(item.get("meaning"))
        example = one_line(item.get("example"))

        vocab_items.append(
            '<li class="vocab-item">'
            f'<div class="vocab-word"><strong>{esc(word)}</strong>'
            + (f'<span class="phonetic">{esc(phonetic)}</span>' if phonetic else "")
            + "</div>"
            + (f'<p class="vocab-meaning">{esc(meaning)}</p>' if meaning else "")
            + (f'<p class="vocab-example">{esc(example)}</p>' if example else "")
            + "</li>"
        )
    vocab_html = "".join(vocab_items) or '<li class="vocab-item muted">暂无词汇</li>'

    vocab_task = one_line(english.get("vocab_task"))
    vocab_task_html = f'<p class="task-line">{esc(vocab_task)}</p>' if vocab_task else ""

    # --- 对话 ---
    dialogue = english.get("dialogue") or {}
    scenario = ""
    raw_lines: list = []
    if isinstance(dialogue, dict):
        scenario = one_line(dialogue.get("scenario"))
        raw_lines = dialogue.get("lines") or []
    elif isinstance(dialogue, list):
        raw_lines = dialogue

    bubbles = []
    for index, line in enumerate(raw_lines):
        if not isinstance(line, dict):
            continue
        speaker = one_line(line.get("speaker")) or ("B" if index % 2 else "A")
        en = one_line(line.get("en"))
        zh = one_line(line.get("zh"))
        if not (en or zh):
            continue
        side = " bubble-right" if index % 2 else " bubble-left"
        bubbles.append(
            f'<div class="bubble{side}">'
            f'<span class="bubble-speaker">{esc(speaker)}</span>'
            + (f'<p class="bubble-en">{esc(en)}</p>' if en else "")
            + (f'<p class="bubble-zh">{esc(zh)}</p>' if zh else "")
            + "</div>"
        )
    dialogue_html = "".join(bubbles) or '<p class="muted">暂无对话</p>'
    scenario_html = f'<p class="scenario-line">{esc(scenario)}</p>' if scenario else ""

    # --- 语法 ---
    grammar = english.get("grammar") or {}
    point = one_line(grammar.get("point"))
    rule = one_line(grammar.get("rule"))
    examples = grammar.get("examples") or []
    if isinstance(examples, str):
        examples = [examples]
    example_items = "".join(
        f"<li>{esc(one_line(example))}</li>" for example in examples if one_line(example)
    )

    grammar_point_html = f'<p class="grammar-point">{esc(point)}</p>' if point else ""
    grammar_rule_html = f'<p class="field-text">{esc(rule)}</p>' if rule else ""
    grammar_examples_html = (
        f'<ul class="example-list">{example_items}</ul>' if example_items else ""
    )

    # --- 听力 ---
    listening = english.get("listening") or {}
    material = one_line(listening.get("material"))
    listen_task = one_line(listening.get("task"))
    tip = one_line(listening.get("tip"))

    material_html = (
        f'<p class="field-text"><strong>材料：</strong>{esc(material)}</p>'
        if material
        else ""
    )
    listen_task_html = (
        f'<p class="field-text"><strong>任务：</strong>{esc(listen_task)}</p>'
        if listen_task
        else ""
    )
    tip_html = f'<p class="tip-line"><strong>技巧：</strong>{esc(tip)}</p>' if tip else ""

    return (
        '<article class="card card-english">'
        + card_head("🌍", "04 · 多国语言", "英语精进 · 每天 20 分钟")
        + '<div class="english-grid">'
        + '<section class="english-block">'
        + "<h3>金山词霸 · 词汇任务</h3>"
        + vocab_task_html
        + f'<ul class="vocab-list">{vocab_html}</ul>'
        + "</section>"
        + '<section class="english-block">'
        + "<h3>AI Talk · 情景对话</h3>"
        + scenario_html
        + f'<div class="dialogue">{dialogue_html}</div>'
        + "</section>"
        + '<section class="english-block">'
        + "<h3>语法点</h3>"
        + grammar_point_html
        + grammar_rule_html
        + grammar_examples_html
        + "</section>"
        + '<section class="english-block">'
        + "<h3>听力跟读</h3>"
        + material_html
        + listen_task_html
        + tip_html
        + "</section>"
        + "</div>"
        + "</article>"
    )


def render_cards(data: dict) -> str:
    return "\n".join(
        [
            render_skills(data.get("skills") or {}),
            render_wealth(data.get("wealth") or {}),
            render_cognition(data.get("cognition") or {}),
            render_english(data.get("english") or {}),
        ]
    )


def build_summary(data: dict) -> str:
    """用于 meta / 首页列表的一句话摘要。"""
    parts: list[str] = []
    if one_line(data.get("one_line")):
        parts.append(one_line(data.get("one_line")))

    skills = data.get("skills") or {}
    wealth = data.get("wealth") or {}
    cognition = data.get("cognition") or {}
    english = data.get("english") or {}
    grammar = english.get("grammar") or {}

    if one_line(skills.get("topic")):
        parts.append(f"技能：{one_line(skills.get('topic'))}")
    if one_line(wealth.get("topic")):
        parts.append(f"财富：{one_line(wealth.get('topic'))}")
    if one_line(cognition.get("model")):
        parts.append(f"认知：{one_line(cognition.get('model'))}")
    if one_line(grammar.get("point")):
        parts.append(f"英语：{one_line(grammar.get('point'))}")

    return " ｜ ".join(parts)


def render_report(data: dict, template: str, date_str: str, weekday: str, now: datetime) -> str:
    one_line_text = one_line(data.get("one_line"))
    summary = build_summary(data)

    replacements = {
        "PAGE_TITLE": esc(f"个人成长日报 · {date_str}"),
        "DATE": esc(date_str),
        "WEEKDAY": esc(weekday),
        "ONE_LINE": esc(one_line_text),
        "SUMMARY_ATTR": esc(summary),
        "DATA": render_cards(data),
        "TOMORROW": esc(one_line(data.get("tomorrow_preview"))),
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

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(BEIJING)
    date_str = now.strftime("%Y-%m-%d")
    weekday = WEEKDAYS[now.weekday()]

    recent_topics = load_recent_topics()
    if recent_topics:
        print(f"[info] 已加载 {len(recent_topics)} 条近期主题用于去重", file=sys.stderr)

    user_prompt = build_user_prompt(date_str, weekday, recent_topics)

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=2)
        data = call_claude(client, system_prompt, user_prompt)
    except anthropic.AuthenticationError:
        print("[error] ANTHROPIC_API_KEY 无效或已过期", file=sys.stderr)
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

    try:
        page = render_report(data, template, date_str, weekday, now)
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
