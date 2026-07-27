"""Bounded, structured candidate-news precheck for strategy research."""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

if __package__ and __package__.startswith("app."):
    from ..core.model_api import build_model_request, request_model
else:
    from core.model_api import build_model_request, request_model


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_MAX_CANDIDATES = 5
TONE_LABELS = ("利好", "利空", "中性")
TONE_VALUES = {"利好": "positive", "利空": "negative", "中性": "neutral"}
NEGATED_TONE_GROUP_RE = re.compile(
    r"(?:未发现|未见|暂无|没有|并无|无|不存在|不构成|未构成|不属于|"
    r"不能判断为|难以判断为|并非|不是)"
    r"\s*(?:其他|任何|更多|新增)?\s*"
    r"(?:(?:明确|重大|明显|实质性|直接|潜在)\s*)*"
    r"(?:利好|利空|中性)"
    r"(?:\s*(?:或|和|及|、|/)\s*"
    r"(?:(?:明确|重大|明显|实质性|直接|潜在)\s*)*"
    r"(?:利好|利空|中性))*"
    r"\s*(?:消息|因素|事项|影响|事件|信号)?"
)
CONCLUSIVE_TONE_RE = re.compile(
    r"(?:构成|属于|判断为|评估为|结论(?:为|是)|"
    r"(?:整体|总体)(?:判断为|评估为|偏向|呈现)?|偏向)"
    r"\s*(?:(?:近期|短期|中长期|重大|明显|实质性|直接|总体|整体)\s*)*"
    r"(利好|利空|中性)"
)


def _bounded_int(
    values: Mapping[str, Any],
    name: str,
    default: int,
    low: int,
    high: int,
) -> int:
    try:
        value = int(str(values.get(name) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _token_count(value: Any, default: int = 4096) -> int:
    compact = str(value or "").replace(",", "").replace("_", "").strip()
    matched = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", compact)
    if not matched:
        return default
    number = float(matched.group(1))
    unit = matched.group(2).lower()
    multiplier = 1_000_000 if unit == "m" else 1_000 if unit == "k" else 1
    return max(256, min(12000, int(number * multiplier)))


@dataclass(frozen=True)
class NewsPrecheckConfig:
    base_url: str
    api_key: str
    model: str
    api_mode: str = "auto"
    timeout_seconds: int = 45
    max_requests: int = 1
    concurrency: int = 5
    max_tokens: int = 4096

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "NewsPrecheckConfig | None":
        base_url = str(values.get("DASHBOARD_NEWS_BASE_URL") or "").strip().rstrip("/")
        api_key = str(values.get("DASHBOARD_NEWS_API_KEY") or "").strip()
        model = str(values.get("DASHBOARD_NEWS_MODEL") or "").strip()
        if not any((base_url, api_key, model)):
            return None
        missing = [
            name
            for name, value in (
                ("DASHBOARD_NEWS_BASE_URL", base_url),
                ("DASHBOARD_NEWS_API_KEY", api_key),
                ("DASHBOARD_NEWS_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise ValueError("incomplete_news_precheck_config:" + ",".join(missing))
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            api_mode=str(values.get("DASHBOARD_NEWS_API_MODE") or "auto").strip() or "auto",
            timeout_seconds=_bounded_int(values, "DASHBOARD_NEWS_TIMEOUT", 45, 5, 120),
            max_requests=_bounded_int(values, "DASHBOARD_NEWS_MAX_RETRIES", 1, 1, 3),
            concurrency=_bounded_int(values, "DASHBOARD_NEWS_CONCURRENCY", 5, 1, 5),
            max_tokens=_token_count(values.get("DASHBOARD_NEWS_MAX_TOKENS"), 4096),
        )


def candidate_label(candidate: Mapping[str, Any]) -> str:
    code = str(candidate.get("code") or "").strip()
    name = str(candidate.get("name") or "").strip()
    return " ".join(part for part in (code, name) if part) or "未知股票"


def news_search_tools(model: str, api_mode: str = "auto") -> list[dict[str, str]]:
    """Return search tools supported by the configured news-precheck model.

    X's dedicated search tool is an xAI/Grok capability.  Other providers keep
    using web search for publicly indexed X and Xueqiu pages instead of making
    the independent news-precheck configuration depend on the Grok settings.
    """

    tools = [{"type": "web_search"}]
    normalized_model = str(model or "").strip().lower()
    normalized_mode = str(api_mode or "auto").strip().lower().replace("-", "_")
    if normalized_model.startswith("grok-") and (
        normalized_mode in {"responses", "response"}
        or normalized_model.startswith("grok-4.5")
    ):
        tools.append({"type": "x_search"})
    return tools


def build_candidate_news_prompt(candidate: Mapping[str, Any]) -> str:
    return f"""搜索以下A股最近3天的重大消息与市场舆情，只针对这一只股票：
{candidate_label(candidate)}

请交叉核验三类公开来源：公司公告或交易所披露、主流财经媒体、雪球与X/Twitter公开内容。
公告和主流财经媒体用于确认事实；雪球和X只用于概括市场观点，不得把未经证实的帖子当作公司事实。无法访问某个平台或没有可核验内容时，明确写“未见显著讨论”，不要编造。

输出格式（单行纯文本，50至120字）：
事件：核心事实；影响：对公司的直接影响；舆情：雪球和X的代表性倾向或未见显著讨论（利好/利空/中性）
如没有明确重大消息，输出：
事件：未发现明确重大消息；影响：暂无；舆情：雪球和X未见显著讨论（中性）
不要重复股票代码和名称，不要输出帖子原文、用户名、引用编号、链接、来源列表、检索日期、当前日期、检索过程或免责声明，不要使用 Markdown。"""


def request_candidate_news(candidate: Mapping[str, Any], config: NewsPrecheckConfig) -> str:
    model_request = build_model_request(
        config.base_url,
        config.model,
        [{"role": "user", "content": build_candidate_news_prompt(candidate)}],
        max_tokens=config.max_tokens,
        api_mode=config.api_mode,
        tools=news_search_tools(config.model, config.api_mode),
        reasoning={"effort": "low"},
        stream=False,
        extra_payload={"stream": False},
    )
    last_error: Exception | None = None
    for attempt in range(config.max_requests):
        try:
            parsed = request_model(
                model_request,
                config.api_key,
                timeout=config.timeout_seconds,
            )
            content = str(parsed.content or "").strip()
            if not content:
                raise ValueError("empty_news_precheck_response")
            return content
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < config.max_requests:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"news_precheck_{type(last_error).__name__}")


def parse_chat_completion_content(raw: str) -> str:
    """Read visible content from JSON or OpenAI-compatible SSE responses."""
    if not str(raw or "").strip():
        raise ValueError("empty_news_precheck_response")
    if raw.lstrip().startswith("data:"):
        parts: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            choice = (parsed.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            parts.append(str(delta.get("content") or message.get("content") or ""))
        return "".join(parts)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_news_precheck_response") from exc
    choice = (parsed.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return str(message.get("content") or "")


def _clean_candidate_news_text(content: Any) -> str:
    text = str(content or "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[\s*\[?\d+\]?\s*\]\s*\(\s*https?://[^)]+\)", "", text)
    text = re.sub(r"\[\[?\d+\]?\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\(?https?://[^\s)]+\)?", "", text)
    text = re.sub(r"\*\*|__|~~|`", "", text)
    text = re.sub(r"^\s{0,3}(?:#{1,6}\s+|>\s*|[-+]\s+)", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"^(?:代码\s*名称|股票代码\s*股票名称)\s*[：:]\s*",
        "",
        text,
    )
    text = re.sub(r"^[-+]\s+", "", text).strip()
    text = re.sub(
        r"\s*[（(](?:此为|注\s*[：:]?|说明\s*[：:]?)[^）)]*[）)]\s*$",
        "",
        text,
    ).strip()
    return text


def _candidate_news_tone_label(text: str) -> str:
    explicit = re.findall(r"[（(](利好|利空|中性)[）)]", text)
    if explicit:
        return explicit[-1]

    without_negated = NEGATED_TONE_GROUP_RE.sub("", text)
    conclusions = CONCLUSIVE_TONE_RE.findall(without_negated)
    if conclusions:
        return conclusions[-1]
    present = [label for label in TONE_LABELS if label in without_negated]
    return present[0] if len(present) == 1 else ""


def repair_cached_news_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Locally reclassify a substantive legacy response without a model call."""

    result = dict(record)
    if (
        result.get("checked") is not True
        or result.get("available") is True
        or result.get("error") != "unclassified_response"
    ):
        return result
    text = _clean_candidate_news_text(result.get("summary"))
    label = _candidate_news_tone_label(text)
    tone = TONE_VALUES.get(label)
    if not text or tone is None:
        return result
    result.update({
        "available": True,
        "tone": tone,
        "tone_label": label,
        "summary": text[:600],
        "error": "",
        "repaired_locally": True,
    })
    return result


def parse_candidate_news_record(
    candidate: Mapping[str, Any],
    content: str,
    *,
    fetched_at: str,
) -> dict[str, Any]:
    text = _clean_candidate_news_text(content)
    label = _candidate_news_tone_label(text)
    tone = TONE_VALUES.get(label)
    tone_matches = list(re.finditer(r"[（(](?:利好|利空|中性)[）)]", text))
    if tone_matches:
        text = text[: tone_matches[-1].end()].strip()
    return {
        "code": str(candidate.get("code") or ""),
        "name": str(candidate.get("name") or ""),
        "checked": True,
        "available": tone is not None,
        "tone": tone or "neutral",
        "tone_label": label or "未识别",
        "summary": text[:600],
        "source_scope": ["disclosures", "financial_media", "xueqiu", "x"],
        "window_days": 3,
        "fetched_at": fetched_at,
        "error": "" if tone is not None else "unclassified_response",
    }


def fetch_candidate_news_records(
    candidates: list[dict[str, Any]],
    config: NewsPrecheckConfig,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    requester: Callable[[Mapping[str, Any], NewsPrecheckConfig], str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    selected = [item for item in candidates[:max_candidates] if isinstance(item, dict)]
    if not selected:
        return []
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CN_TZ)
    fetched_at = current.astimezone(CN_TZ).isoformat(timespec="seconds")
    active_requester = requester or request_candidate_news
    results: list[dict[str, Any] | None] = [None] * len(selected)

    def fetch(index: int, candidate: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            content = active_requester(candidate, config)
            return index, parse_candidate_news_record(candidate, content, fetched_at=fetched_at)
        except Exception as exc:
            return index, {
                "code": str(candidate.get("code") or ""),
                "name": str(candidate.get("name") or ""),
                "checked": True,
                "available": False,
                "tone": "neutral",
                "tone_label": "不可用",
                "summary": "",
                "window_days": 3,
                "fetched_at": fetched_at,
                "error": f"request_{type(exc).__name__}",
            }

    workers = min(config.concurrency, len(selected))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, index, candidate) for index, candidate in enumerate(selected)]
        for future in concurrent.futures.as_completed(futures):
            index, record = future.result()
            results[index] = record
    return [record for record in results if isinstance(record, dict)]


def format_cached_news_record(record: Mapping[str, Any]) -> str:
    if record.get("available") and str(record.get("summary") or "").strip():
        summary = str(record.get("summary") or "").strip().lstrip("- ")
        code = str(record.get("code") or "").strip()
        name = str(record.get("name") or "").strip()
        if summary.startswith(candidate_label(record)) or (
            code and summary.startswith(code)
        ) or (name and summary.startswith(name)):
            return f"- {summary}"
        return f"- {candidate_label(record)}：{summary}"
    return (
        f"- {candidate_label(record)}：消息面预检失败"
        f"（{record.get('error') or 'unavailable'}）"
    )


def format_cached_news_records(records: list[Mapping[str, Any]]) -> str:
    lines = [format_cached_news_record(record) for record in records]
    return "【消息面预检（扫描阶段缓存）】\n" + "\n".join(lines) if lines else ""
