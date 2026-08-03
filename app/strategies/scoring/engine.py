"""Pure orchestration for running and comparing registered strategy scorers."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


StrategyScorer = Callable[..., dict[str, Any] | None]


def invoke_strategy_scorer(
    scorer: StrategyScorer,
    rows: Sequence[Mapping[str, Any]],
    context: dict[str, Any] | None = None,
    *,
    shared_inputs: dict[Callable[..., Any], Any] | None = None,
) -> dict[str, Any] | None:
    """Invoke one scorer while reusing an explicitly declared row input.

    A scorer may expose ``shared_input_builder`` and
    ``shared_input_keyword`` attributes.  Runners keep the resulting value for
    the current stock only and pass it back by keyword to every scorer that
    declares the same builder.  Ordinary and third-party scorers retain their
    existing call signature.
    """
    keyword_args: dict[str, Any] = {}
    builder = getattr(scorer, "shared_input_builder", None)
    keyword = str(getattr(scorer, "shared_input_keyword", "") or "").strip()
    if callable(builder) and keyword:
        cache = shared_inputs if shared_inputs is not None else {}
        if builder not in cache:
            cache[builder] = builder(rows)
        keyword_args[keyword] = cache[builder]
    if getattr(scorer, "requires_context", False):
        return scorer(rows, context or {}, **keyword_args)
    return scorer(rows, **keyword_args)


def analyze_enriched_rows(
    rows: list[dict[str, Any]],
    scorers: Mapping[str, StrategyScorer],
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run scorers against enriched OHLCV rows and choose the best result.

    Legacy scorers keep their one-argument API. Cross-sectional strategies opt
    into the shared scan context by setting ``requires_context = True``.
    """
    strategies: dict[str, dict[str, Any]] = {}
    shared_inputs: dict[Callable[..., Any], Any] = {}
    for strategy_id, scorer in scorers.items():
        # Each scorer may annotate rows, so isolate its shallow mutations.
        scorer_rows = [dict(row) for row in rows]
        scored = invoke_strategy_scorer(
            scorer,
            scorer_rows,
            context,
            shared_inputs=shared_inputs,
        )
        if scored:
            strategies[strategy_id] = scored

    if not strategies:
        return None

    # Match the execution/backtest path: an actionable strategy must not be
    # hidden by a higher-scoring alternative that its own hard gates reject.
    # If no strategy is actionable, retain the strongest blocked/watch result
    # for diagnostics and display.
    def best_strategy_key(name: str) -> tuple[int, int, float, int]:
        item = strategies[name]
        score = float(item.get("score") or 0)
        threshold = float(item.get("entry_threshold") or 8)
        priority = int(item.get("strategy_priority") or 0)
        blockers = item.get("hard_blockers") or []
        explicit_actionable = item.get("actionable")
        actionable = bool(
            (explicit_actionable if explicit_actionable is not None else True)
            and score >= threshold
            and not blockers
        )
        return (
            1 if actionable else 0,
            1 if score >= threshold else 0,
            score,
            priority,
        )

    best_name = max(strategies, key=best_strategy_key)
    best_score = strategies[best_name]["score"]
    best_verdict = strategies[best_name]["verdict"]
    best_decision_score = strategies[best_name].get("decision_score", best_score)

    consensus_count = sum(1 for strategy in strategies.values() if strategy["score"] >= 7)
    consensus_boost = 1 if consensus_count >= 3 else (0.5 if consensus_count >= 2 else 0)

    return {
        "best_strategy": best_name,
        "best_score": best_score,
        "best_decision_score": best_decision_score,
        "best_verdict": best_verdict,
        "strategies": strategies,
        "consensus_count": consensus_count,
        "consensus_boost": consensus_boost,
    }
