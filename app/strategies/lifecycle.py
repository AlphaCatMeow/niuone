"""Causal lifecycle semantics for the NiuOne mainline strategy."""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


NIUONE_LIFECYCLE_STAGE_ORDER = (
    "brewing",
    "markup",
    "climax",
    "divergence",
    "fade",
)
NIUONE_LIFECYCLE_CLIMAX_SCORE = 78.0
NIUONE_LIFECYCLE_ACTION_LABELS: Mapping[str, str] = MappingProxyType({
    "niu_reversal_probe": "牛牛试仓",
    "niu_emerging": "牛牛启动",
    "niu_leader": "牛牛领涨",
    "niu_pullback": "牛牛转强",
})

NIUONE_LIFECYCLE_STAGES: Mapping[str, Mapping[str, Any]] = MappingProxyType({
    "brewing": MappingProxyType({
        "label": "主线酝酿",
        "order": 10,
        "entry_policy": "probe_only",
        "allowed_entry_strategy_ids": ("niu_reversal_probe",),
        "description": "候选题材开始形成多股修复，只允许小仓试错并等待跨日延续。",
    }),
    "markup": MappingProxyType({
        "label": "主线主升",
        "order": 20,
        "entry_policy": "participate",
        "allowed_entry_strategy_ids": (
            "niu_emerging",
            "niu_leader",
        ),
        "description": "题材完成跨日延续或主线确认，只围绕启动、领涨参与趋势。",
    }),
    "climax": MappingProxyType({
        "label": "主线高潮",
        "order": 30,
        "entry_policy": "selective_participation_or_reduce",
        "allowed_entry_strategy_ids": (
            "niu_leader",
            "niu_pullback",
        ),
        "description": "已确认主线进入高强度区，仍可买入满足领涨或企稳转强条件的核心股，并继续按持仓规则锁定利润。",
    }),
    "divergence": MappingProxyType({
        "label": "主线分歧",
        "order": 40,
        "entry_policy": "selective_repair_reclaim_or_reduce",
        "allowed_entry_strategy_ids": (
            "niu_leader",
            "niu_pullback",
        ),
        "description": "主线高位分化或延续减弱，只允许仍保持核心领涨或企稳收复后转强，并根据强弱管理减仓。",
    }),
    "fade": MappingProxyType({
        "label": "主线退幕",
        "order": 50,
        "entry_policy": "exit_only",
        "allowed_entry_strategy_ids": (),
        "description": "主线强度和核心股延续失效，只管理退出，不开新仓。",
    }),
})


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def niuone_lifecycle_stage(values: Mapping[str, Any]) -> str:
    """Map one theme observation to its lifecycle phase.

    ``inactive`` is intentionally left unclassified: without a prior confirmed
    mainline it can mean "never started", not "finished".  The mapper uses no
    future observations and does not alter any entry or exit rule by itself.
    Use :func:`niuone_lifecycle_transition` when a previous theme observation
    is available so a failed markup does not jump backwards into brewing.
    """
    state = str(
        values.get("mainline_state")
        or values.get("sector_status")
        or values.get("state")
        or ""
    )
    persistent = bool(
        values.get("mainline_cross_day_persistent")
        or values.get("cross_day_persistent")
    )
    confirmed = bool(
        values.get("mainline_confirmed")
        or values.get("mainline_cross_day_confirmed")
        or values.get("cross_day_confirmed")
    )
    score = _number(
        values.get("mainline_score")
        if values.get("mainline_score") is not None
        else values.get("score")
    )
    if state == "candidate":
        return "brewing"
    if state == "emerging":
        return "markup" if persistent else "brewing"
    if state == "mainline":
        return (
            "climax"
            if confirmed
            and score is not None
            and score >= NIUONE_LIFECYCLE_CLIMAX_SCORE
            else "markup"
        )
    if state == "diverging":
        return "divergence"
    if state == "fading":
        return "fade"
    return ""


def niuone_lifecycle_transition(
    previous: Mapping[str, Any] | None,
    values: Mapping[str, Any],
) -> str:
    """Resolve a causal lifecycle transition with bounded hysteresis.

    The raw theme classifier can lose one-day core continuity and move an
    ``emerging`` observation from markup back to brewing.  Once markup has
    started that is a divergence observation, not a second brewing phase.
    Divergence persists until the classifier explicitly reports fading or
    inactive; a valid mainline may instead resolve it back into markup or
    climax.  Fade remains absorbing while weakness persists.  Once a fading or
    inactive observation is followed by renewed candidate/mainline evidence,
    that observation starts a new causal episode at its current stage.
    """
    recorded_current = str(
        values.get("niuone_lifecycle_stage") or ""
    ).strip()
    current = (
        recorded_current
        if recorded_current in NIUONE_LIFECYCLE_STAGES
        else niuone_lifecycle_stage(values)
    )
    previous = previous if isinstance(previous, Mapping) else {}
    previous_stage = str(previous.get("niuone_lifecycle_stage") or "")
    if previous_stage not in NIUONE_LIFECYCLE_STAGES:
        previous_stage = ""
    if not previous_stage:
        previous_stage = niuone_lifecycle_stage(previous)
    if not previous_stage:
        return current

    current_state = str(
        values.get("mainline_state")
        or values.get("sector_status")
        or values.get("state")
        or ""
    )
    previous_state = str(
        previous.get("mainline_state")
        or previous.get("sector_status")
        or previous.get("state")
        or ""
    )
    if previous_stage == "fade":
        if (
            previous_state in {"fading", "inactive"}
            and current in {"brewing", "markup", "climax"}
        ):
            return current
        return "fade"
    if not current:
        return "fade" if current_state == "inactive" else previous_stage
    if previous_stage == "markup" and current == "brewing":
        return "divergence"
    if previous_stage == "climax" and current in {"brewing", "markup"}:
        return "divergence"
    if previous_stage == "divergence" and current == "brewing":
        return "divergence"
    return current


def niuone_lifecycle_metadata(
    values: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return canonical metadata without discarding an observed causal stage."""
    recorded_stage = str(
        values.get("niuone_lifecycle_stage") or ""
    ).strip()
    stage = (
        niuone_lifecycle_transition(previous, values)
        if previous is not None
        else recorded_stage
        if recorded_stage in NIUONE_LIFECYCLE_STAGES
        else niuone_lifecycle_stage(values)
    )
    definition = NIUONE_LIFECYCLE_STAGES.get(stage) or {}
    return {
        "niuone_lifecycle_stage": stage,
        "niuone_lifecycle_label": str(definition.get("label") or ""),
        "niuone_lifecycle_order": definition.get("order"),
        "niuone_lifecycle_entry_policy": str(
            definition.get("entry_policy") or ""
        ),
    }


def niuone_lifecycle_entry_blocker(
    strategy_id: str,
    values: Mapping[str, Any],
) -> str | None:
    """Return the canonical production blocker for a stage/action mismatch."""
    stage = str(values.get("niuone_lifecycle_stage") or "").strip()
    if not stage:
        stage = niuone_lifecycle_stage(values)
    definition = NIUONE_LIFECYCLE_STAGES.get(stage)
    if definition is None:
        return "牛牛主线阶段不可识别，禁止按五阶段契约开新仓"
    allowed = tuple(definition.get("allowed_entry_strategy_ids") or ())
    normalized_strategy_id = str(strategy_id or "").strip()
    if normalized_strategy_id not in allowed:
        label = str(definition.get("label") or stage)
        action_label = NIUONE_LIFECYCLE_ACTION_LABELS.get(
            normalized_strategy_id,
            normalized_strategy_id or "未知动作",
        )
        return f"{label}阶段不允许{action_label}开新仓"
    return None


__all__ = [
    "NIUONE_LIFECYCLE_STAGE_ORDER",
    "NIUONE_LIFECYCLE_STAGES",
    "NIUONE_LIFECYCLE_CLIMAX_SCORE",
    "NIUONE_LIFECYCLE_ACTION_LABELS",
    "niuone_lifecycle_entry_blocker",
    "niuone_lifecycle_metadata",
    "niuone_lifecycle_stage",
    "niuone_lifecycle_transition",
]
