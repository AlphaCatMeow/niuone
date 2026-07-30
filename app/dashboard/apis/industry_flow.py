"""Build a chart-ready industry fund-flow pressure snapshot.

The upstream data identifies industries with net inflow or net outflow, but it
does not identify an industry-to-industry transfer path.  The payload therefore
models only the observable pressure on each industry: positive rows are being
filled and negative rows are being drained.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any


DEFAULT_SIDE_LIMIT = 10
DEFAULT_HISTORY_LIMIT = 300
EXPECTED_TRADING_DAY_POINTS = 249
DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
DEFAULT_PLAYBACK_SPEED = 0.5
SAMPLING_WINDOWS = (("09:25", "11:31"), ("13:00", "15:01"))
MORNING_START_MINUTE = 9 * 60 + 25
MORNING_END_MINUTE = 11 * 60 + 31
AFTERNOON_START_MINUTE = 13 * 60
AFTERNOON_END_MINUTE = 15 * 60 + 1
# Same-day equal-chip volume model: open chips are equal; net only reshapes size.
VOLUME_BASE = 1.0
VOLUME_ALPHA = 0.8
VOLUME_SCALE_MIN_YI = 8.0


def _finite_number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rounded(value: float) -> float:
    return round(float(value), 4)


def _normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    name = str(row.get("name") or row.get("industry") or "").strip()
    net_flow = _finite_number(row.get("net_flow_yi"))
    if not name or net_flow is None or abs(net_flow) < 1e-9:
        return None

    inflow = _finite_number(row.get("inflow_yi"))
    outflow = _finite_number(row.get("outflow_yi"))
    if inflow is None and outflow is None:
        # Main-net snapshots intentionally do not claim observable gross-flow
        # endpoints.  Keep the compatibility fields non-negative without
        # inventing a synthetic baseline.
        if net_flow > 0:
            outflow, inflow = 0.0, net_flow
        else:
            inflow, outflow = 0.0, abs(net_flow)
    elif inflow is None:
        outflow = max(0.0, float(outflow or 0.0))
        inflow = max(0.0, outflow + net_flow)
    elif outflow is None:
        inflow = max(0.0, float(inflow or 0.0))
        outflow = max(0.0, inflow - net_flow)
    else:
        inflow = max(0.0, inflow)
        outflow = max(0.0, outflow)

    pct = _finite_number(row.get("pct"))
    return {
        "name": name,
        "net_flow_yi": net_flow,
        "inflow_yi": inflow,
        "outflow_yi": outflow,
        "pct": pct,
        "leader": str(row.get("leader") or "").strip(),
    }


def _normalized_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    deduplicated: dict[str, dict[str, Any]] = {}
    for key in ("inflow", "outflow", "items"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = _normalize_row(raw)
            if row is None:
                continue
            previous = deduplicated.get(row["name"])
            if previous is None or abs(row["net_flow_yi"]) > abs(previous["net_flow_yi"]):
                deduplicated[row["name"]] = row
    return sorted(deduplicated.values(), key=lambda row: row["name"])


def _visible_rows(payload: dict[str, Any], side_limit: int) -> list[dict[str, Any]]:
    normalized = _normalized_rows(payload)

    positive = sorted(
        (row for row in normalized if row["net_flow_yi"] > 0),
        key=lambda row: (-row["net_flow_yi"], row["name"]),
    )[:side_limit]
    negative = sorted(
        (row for row in normalized if row["net_flow_yi"] < 0),
        key=lambda row: (row["net_flow_yi"], row["name"]),
    )[:side_limit]
    return [*negative, *positive]


def _node_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return f"sector-{digest}"


def _sample_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _clock_minute(value: Any) -> int | None:
    try:
        parsed = datetime.strptime(str(value or "").strip(), "%H:%M")
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def normalize_industry_flow_sampling_windows(
    windows: Any = None,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Validate and normalize two ordered, non-overlapping Beijing windows."""

    source = SAMPLING_WINDOWS if windows is None else windows
    try:
        pairs = tuple((str(start).strip(), str(end).strip()) for start, end in source)
    except (TypeError, ValueError):
        raise ValueError("资金流采样窗口需要上午、下午两个时间段") from None
    if len(pairs) != 2:
        raise ValueError("资金流采样窗口需要上午、下午两个时间段")
    minutes = [(_clock_minute(start), _clock_minute(end)) for start, end in pairs]
    if any(start is None or end is None for start, end in minutes):
        raise ValueError("资金流采样时间请使用北京时间 HH:MM")
    morning_start, morning_end = minutes[0]
    afternoon_start, afternoon_end = minutes[1]
    if not (morning_start < morning_end < afternoon_start < afternoon_end):
        raise ValueError("资金流采样时间需满足：上午开始 < 上午结束 < 下午开始 < 下午结束")
    return pairs  # type: ignore[return-value]


def _sampling_window_minutes(
    windows: Any = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    normalized = normalize_industry_flow_sampling_windows(windows)
    return tuple(
        (_clock_minute(start) or 0, _clock_minute(end) or 0)
        for start, end in normalized
    )  # type: ignore[return-value]


def is_industry_flow_session_timestamp(value: Any, *, sampling_windows: Any = None) -> bool:
    """Return whether a timestamp falls in either A-share sampling session."""

    sample_time = value if isinstance(value, datetime) else _sample_time(value)
    if sample_time is None:
        return False
    minute = sample_time.hour * 60 + sample_time.minute
    return any(
        start_minute <= minute <= end_minute
        for start_minute, end_minute in _sampling_window_minutes(sampling_windows)
    )


def _window_point_count(start_minute: int, end_minute: int, interval_seconds: int) -> int:
    window_seconds = (end_minute - start_minute) * 60
    return window_seconds // interval_seconds + 1


def _session_point_count(interval_seconds: int, sampling_windows: Any = None) -> int:
    return sum(
        _window_point_count(start_minute, end_minute, interval_seconds)
        for start_minute, end_minute in _sampling_window_minutes(sampling_windows)
    )


def _expected_samples_through(
    value: Any,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    sampling_windows: Any = None,
) -> int:
    """Return the number of configured sample boundaries elapsed in the session."""

    sample_time = _sample_time(value)
    if sample_time is None:
        return 0
    interval = max(DEFAULT_SAMPLE_INTERVAL_SECONDS, int(interval_seconds))
    second_of_day = sample_time.hour * 3600 + sample_time.minute * 60 + sample_time.second
    (morning_start, morning_end), (afternoon_start, afternoon_end) = (
        _sampling_window_minutes(sampling_windows)
    )
    morning_start_seconds = morning_start * 60
    morning_end_seconds = morning_end * 60
    afternoon_start_seconds = afternoon_start * 60
    afternoon_end_seconds = afternoon_end * 60
    morning_points = _window_point_count(morning_start, morning_end, interval)
    afternoon_points = _window_point_count(afternoon_start, afternoon_end, interval)
    if second_of_day < morning_start_seconds:
        return 0
    if second_of_day <= morning_end_seconds:
        return (second_of_day - morning_start_seconds) // interval + 1
    if second_of_day < afternoon_start_seconds:
        return morning_points
    if second_of_day <= afternoon_end_seconds:
        return morning_points + (second_of_day - afternoon_start_seconds) // interval + 1
    return morning_points + afternoon_points


def compact_industry_flow_sample(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return one durable, normalized upstream sample."""

    source = payload if isinstance(payload, dict) else {}
    generated_at = str(source.get("generated_at") or "").strip()
    if _sample_time(generated_at) is None:
        return None
    rows = _normalized_rows(source)
    if not rows:
        return None
    return {
        "generated_at": generated_at,
        "items": [{
            "name": row["name"],
            "net_flow_yi": _rounded(row["net_flow_yi"]),
            "inflow_yi": _rounded(row["inflow_yi"]),
            "outflow_yi": _rounded(row["outflow_yi"]),
            "pct": None if row["pct"] is None else round(row["pct"], 2),
            "leader": row["leader"],
        } for row in rows],
    }


def append_industry_flow_sample(
    history: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    *,
    max_points: int = DEFAULT_HISTORY_LIMIT,
    interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Append one sample, keeping only the current trading day's bounded history."""

    sample = compact_industry_flow_sample(payload)
    existing = history if isinstance(history, dict) else {}
    if sample is None:
        return existing
    sample_day = sample["generated_at"][:10]
    by_time: dict[str, dict[str, Any]] = {}
    for raw in existing.get("samples") or []:
        compact = compact_industry_flow_sample(raw if isinstance(raw, dict) else None)
        if compact is None or compact["generated_at"][:10] != sample_day:
            continue
        by_time[compact["generated_at"]] = compact
    by_time[sample["generated_at"]] = sample
    limit = max(2, min(600, int(max_points)))
    samples = [by_time[key] for key in sorted(by_time)][-limit:]
    return {
        "schema_version": 1,
        "date": sample_day,
        "interval_seconds": max(DEFAULT_SAMPLE_INTERVAL_SECONDS, int(interval_seconds)),
        "samples": samples,
    }


def _node_from_row(row: dict[str, Any]) -> dict[str, Any]:
    net_flow = float(row["net_flow_yi"])
    return {
        "id": _node_id(row["name"]),
        "name": row["name"],
        "role": "inflow" if net_flow > 0 else "outflow",
        "net_flow_yi": _rounded(net_flow),
        # Pressure ranking still uses absolute net; bubble area uses volume_yi.
        "magnitude_yi": _rounded(abs(net_flow)),
        "inflow_yi": _rounded(row["inflow_yi"]),
        "outflow_yi": _rounded(row["outflow_yi"]),
        "pct": None if row["pct"] is None else round(row["pct"], 2),
        "leader": row["leader"],
        # Filled by _apply_equal_chip_volumes() against the same-day visible field.
        "base_volume_yi": VOLUME_BASE,
        "volume_scale_yi": VOLUME_SCALE_MIN_YI,
        "volume_score": 0.0,
        "volume_yi": VOLUME_BASE,
    }


def _apply_equal_chip_volumes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign same-day equal-chip volumes from net flow only.

    Open chips are equal (volume=1).  Within the currently visible set:
      S = max(S_min, max|net|)
      x = clip(net / S, -1, 1)
      volume = 1 + alpha * x   → about [0.2, 1.8]

    No market-cap and no cross-day comparison: S is recomputed per frame from
    that frame's visible industries only.
    """

    if not nodes:
        return nodes
    scale = max(
        VOLUME_SCALE_MIN_YI,
        max(abs(float(node["net_flow_yi"])) for node in nodes),
    )
    for node in nodes:
        net_flow = float(node["net_flow_yi"])
        score = max(-1.0, min(1.0, net_flow / scale))
        node["base_volume_yi"] = VOLUME_BASE
        node["volume_scale_yi"] = _rounded(scale)
        node["volume_score"] = _rounded(score)
        node["volume_yi"] = _rounded(VOLUME_BASE + VOLUME_ALPHA * score)
    return nodes


def _totals(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    total_inflow = sum(max(0.0, float(node["net_flow_yi"])) for node in nodes)
    total_outflow = sum(max(0.0, -float(node["net_flow_yi"])) for node in nodes)
    return {
        "visible_inflow_yi": _rounded(total_inflow),
        "visible_outflow_yi": _rounded(total_outflow),
        "visible_balance_yi": _rounded(total_inflow - total_outflow),
        "inflow_count": sum(node["role"] == "inflow" for node in nodes),
        "outflow_count": sum(node["role"] == "outflow" for node in nodes),
    }


def _snapshot_id(generated_at: str, nodes: list[dict[str, Any]]) -> str:
    material = {
        "generated_at": generated_at,
        "nodes": [(node["name"], node["net_flow_yi"], node["inflow_yi"], node["outflow_yi"]) for node in nodes],
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def build_industry_flow_payload(
    money_flow: dict[str, Any] | None,
    *,
    side_limit: int = DEFAULT_SIDE_LIMIT,
    history_samples: list[dict[str, Any]] | None = None,
    sample_interval_seconds: int = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    playback_speed: float = DEFAULT_PLAYBACK_SPEED,
    sampling_windows: Any = None,
) -> dict[str, Any]:
    """Return chart-ready inflow and outflow pressure nodes."""

    source = money_flow if isinstance(money_flow, dict) else {}
    limit = max(1, min(10, int(side_limit)))
    interval = max(DEFAULT_SAMPLE_INTERVAL_SECONDS, min(600, int(sample_interval_seconds)))
    speed = max(0.5, min(10.0, float(playback_speed)))
    windows = normalize_industry_flow_sampling_windows(sampling_windows)
    rows = _visible_rows(source, limit)
    generated_at = str(source.get("generated_at") or "")
    current_day = generated_at[:10]

    timeline_sources = [*(history_samples or []), source]
    day_samples: list[dict[str, Any]] = []
    for raw in timeline_sources:
        compact = compact_industry_flow_sample(raw if isinstance(raw, dict) else None)
        if (
            compact is None
            or not is_industry_flow_session_timestamp(
                compact["generated_at"],
                sampling_windows=windows,
            )
            or (current_day and compact["generated_at"][:10] != current_day)
        ):
            continue
        day_samples.append(compact)
    day_samples.sort(key=lambda sample: sample["generated_at"])
    nodes = _apply_equal_chip_volumes([_node_from_row(row) for row in rows])
    totals = _totals(nodes)
    snapshot_id = _snapshot_id(generated_at, nodes)

    timeline_by_time: dict[str, dict[str, Any]] = {}
    for compact in day_samples:
        # Rank every sample from that sample's shared money-flow snapshot.
        # Using the latest snapshot's names here makes earlier leaders vanish
        # whenever they were not also present in the latest top list.
        frame_rows = _visible_rows(compact, limit)
        frame_nodes = _apply_equal_chip_volumes([_node_from_row(row) for row in frame_rows])
        if not frame_nodes:
            continue
        frame_time = compact["generated_at"]
        timeline_by_time[frame_time] = {
            "generated_at": frame_time,
            "snapshot_id": _snapshot_id(frame_time, frame_nodes),
            "nodes": frame_nodes,
            "totals": _totals(frame_nodes),
        }
    timeline = [timeline_by_time[key] for key in sorted(timeline_by_time)][-DEFAULT_HISTORY_LIMIT:]
    first_sample_at = timeline[0]["generated_at"] if timeline else ""
    last_sample_at = timeline[-1]["generated_at"] if timeline else ""
    expected_points = _expected_samples_through(
        last_sample_at or generated_at,
        interval,
        sampling_windows=windows,
    )
    expected_full_day_points = _session_point_count(interval, windows)
    observed_points = {str(frame["generated_at"]) for frame in timeline}
    coverage_ratio = min(1.0, len(observed_points) / expected_points) if expected_points else 0.0
    source_label = str(source.get("source") or "行业主力净额即时快照").strip()
    result = {
        "schema_version": int(_finite_number(source.get("schema_version")) or 2),
        "available": bool(nodes),
        "generated_at": generated_at,
        "snapshot_id": snapshot_id,
        "metric": str(source.get("metric") or "industry_main_net_flow"),
        "metric_label": str(source.get("metric_label") or "今日主力净额"),
        "source": f"{source_label} + 本地周期采样" if len(timeline) > 1 else source_label,
        "source_url": str(source.get("source_url") or ""),
        "unit": "亿元",
        "nodes": nodes,
        "timeline": timeline,
        # Exact snapshot consumed by the indices/market page.  The frontend
        # reuses it while switching views so both presentations retain the same
        # rows and timestamp instead of issuing parallel upstream queries.
        "money_flow": {
            "schema_version": int(_finite_number(source.get("schema_version")) or 2),
            "metric": str(source.get("metric") or "industry_main_net_flow"),
            "metric_label": str(source.get("metric_label") or "今日主力净额"),
            "source": source_label,
            "source_url": str(source.get("source_url") or ""),
            "generated_at": generated_at,
            "inflow": [dict(row) for row in (source.get("inflow") or []) if isinstance(row, dict)],
            "outflow": [dict(row) for row in (source.get("outflow") or []) if isinstance(row, dict)],
            "count": int(_finite_number(source.get("count")) or 0),
        },
        "sampling": {
            "mode": "local_periodic_snapshots",
            "interval_seconds": interval,
            "timezone": "Asia/Shanghai",
            "windows": [
                {"start": start, "end": end}
                for start, end in windows
            ],
            "point_count": len(timeline),
            "actual_playback": len(timeline) > 1,
            "first_sample_at": first_sample_at,
            "last_sample_at": last_sample_at,
            "expected_points_through_last_sample": expected_points,
            "expected_full_day_points": expected_full_day_points,
            "storage_limit": DEFAULT_HISTORY_LIMIT,
            "coverage_ratio": round(coverage_ratio, 4),
            "volume_model": {
                "mode": "equal_chip_same_day_net",
                "base_volume": VOLUME_BASE,
                "alpha": VOLUME_ALPHA,
                "scale_min_yi": VOLUME_SCALE_MIN_YI,
                "formula": "S=max(S_min, max|net|); volume=1+alpha*clip(net/S,-1,1)",
                "scope": "仅当日可见行业场内归一化，不做跨日对比，不使用流通市值",
            },
            "historical_backfill": {
                "available": False,
                "reason": "当前行业主力净额上游仅提供即时截面；本机首次采样前的数据无法作为真实记录回补。",
            },
        },
        "settings": {
            "side_limit": limit,
            "playback_speed": speed,
        },
        # Kept as an empty compatibility field for cached frontend state from
        # the earlier path-replay version.  No industry pair is inferred.
        "links": [],
        "totals": totals,
        "inference": {
            "enabled": False,
            "method": "开盘等大筹码；体积仅由当日行业主力净额在可见行业内归一化决定",
            "caveat": "不推断行业之间的资金划转关系；各行业开盘体积相同，圆面积只反映当日主力净额相对强弱，不代表市值，也不做跨日对比。",
        },
    }
    if source.get("stale_cache"):
        result["stale_cache"] = True
    if source.get("error"):
        result["error"] = str(source["error"])
    return result
