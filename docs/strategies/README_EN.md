# Strategy Research Guide

[简体中文](README.md) | English

This document describes the signal screening, simulated decision-making, and review mechanisms used for research experiments in NiuOne. These capabilities run only in a local research and simulation environment. They do not connect to brokerage accounts, execute real-money trades, or constitute investment advice.

## 1. Research Workflow

NiuOne divides the research workflow into four layers:

1. **Candidate generation**: Scan public market data using the currently enabled rules to produce samples for observation.
2. **Context enrichment**: Aggregate indices, sectors, capital flows, market activity, and recent news as needed.
3. **Simulated assessment**: Combine rule constraints, account state, and optional model output to generate simulation records.
4. **Archival and review**: Save input summaries, source status, simulated actions, and outcomes for later inspection.

Strategy outputs are experimental signals and must not be used as the basis for real trades. If a market-data or model source is temporarily unavailable, the system records its status and either continues with the currently available information or skips the affected step.

## 2. Independent Strategies

The settings page directly selects one active strategy suite. Basic Strategies, Z-ge, Li Daxiao, Sector Tide, NiuOne Method, and Preset Text are peer, mutually exclusive suites. Each suite independently owns its candidate scope, scoring, entry, exit, sizing, and model-prompt rules. Inactive suites do not enter the current new-position scan or decision context.

Switching suites does not rewrite historical position attribution. The active suite controls new BUYs only; each existing position dynamically loads its original exit discipline from the `strategy_mark` captured at entry. Every scan-and-decision cycle refreshes and evaluates all open positions with local exit rules before processing current-suite candidates or model judgment. SELL/HOLD checks continue when the candidate list is empty or the daily loss budget has fired; that budget pauses new BUYs only. Preset Text uses the basic scan only as a raw candidate pool and applies the text rules as the independent decision policy. Empty text creates no new simulated positions and only performs risk checks on existing holdings.

### 2.1 User Guide: Enabling and Triggering a Strategy

1. Set **Active independent strategy** to **Sector Tide** on the settings page. The corresponding value is `DASHBOARD_ACTIVE_STRATEGY=sector_tide`. This setting is applied at runtime and takes effect on the next scan without restarting the Dashboard.
2. New candidates and model decisions reuse the Practice page's shared schedule. The scheduler inside the Dashboard process reads `DASHBOARD_PRACTICE_SCHEDULE_TIMES`; Sector Tide does not have a separate candidate-scan timer.
3. To run immediately, click **Manually trigger candidate scan and trading strategy** on the practice page. One full cycle performs the market scan, candidate generation, model assessment, and execution-layer risk checks.
4. A 09:25 scan may use the opening-auction result to form candidates, but it cannot simulate a fill during the 09:25–09:30 quiet period. Any executable action is queued for a fresh price, session, and risk check after 09:30.

Scheduling ownership is split between two processes:

| Work | Process | Main settings | Behavior |
|---|---|---|---|
| Candidate scan and model decision | Dashboard | `DASHBOARD_B1_SCHEDULE_ENABLED`, `DASHBOARD_PRACTICE_SCHEDULE_TIMES` | Checks every open position under its original exit discipline first, then sends current-suite candidates into the simulated decision flow; zero-candidate scans still perform position exits |
| Local automatic exits | Cron Scheduler | `DASHBOARD_B3_EXIT_TIME`, `DASHBOARD_TIME_EXIT_TIME` | Refreshes position data at the configured times and checks structural stops, sector deterioration, time boxes, 2R, and 2 ATR rules |

Automatic exits are discrete scheduled checks, not broker-native conditional orders or tick-by-tick monitoring. Refreshing the page only reads state and never creates a simulated fill. Switching away from Sector Tide stops new Sector Tide candidates, while existing Sector Tide positions continue to receive exits according to their stored strategy marks.

## 3. Strategy Suites

| Strategy group | Included proxy signals | Research focus |
|---|---|---|
| Basic Strategies | Breakout confirmation, trend pullback | General technical-pattern observation |
| Z-ge | Shaofu B1, B2 confirmation, B3 continuation, Super B1, exit risk controls | Trend- and timing-oriented rule experiments |
| Li Daxiao | Undervalued blue chips, bottom formation, contrarian sentiment, deleveraging defense | Value- and defense-oriented rule experiments |
| Sector Tide | Main-theme leader, early rotation, freeze recovery | Market regime, industry rotation, and within-sector relative strength |
| NiuOne Method | NiuOne Leader, NiuOne Pullback, NiuOne Emerging | Infers and tracks market mainlines from multi-stock strength resonance |

### 3.1 Basic Strategies

- **Breakout confirmation**: Treat a stable pullback after a platform or previous-high breakout as a trend-confirmation sample.
- **Trend pullback**: Treat a strong-trend security that pulls back without breaking below BBI / EMA as a lower-entry observation sample.

### 3.2 Z-ge Rule Group

This rule group references public methods organized in [`zettaranc-skill`](https://github.com/lululu811/zettaranc-skill) and currently includes:

- **Shaofu B1**: Focuses on a low J value, an upward-shifting N pattern, a low-volume pullback, and BBI constraints.
- **B2 confirmation**: Looks for bullish, higher-volume confirmation after B1 and filters samples that are clearly lagging or too far above BBI.
- **B3 continuation**: Looks for a small bullish candle or doji after B2, as well as a shift from divergence to consensus.
- **Super B1**: Looks for low-volume stabilization after a high-volume breakdown while the J value remains low.

Exit and risk rules include constraints based on the previous low or entry candle, a premature-exit prevention score, staged exits, S1 / S2 / S3 top-escape signals, distribution patterns, white-line / BBI breakdowns, peak drawdown, ATR chandelier protection, and timing constraints for B2, B3, and Super B1.

Shaofu B1 uses a hold-state machine that separates hard exits from confirmed soft exits. A new position receives at least three A-share trading days of observation, and only close-confirmed structural stops, a white-line/yellow-line dead cross, two consecutive white-line breaks, or high-confidence distribution may exit during the first 30 minutes after the open. Profit-to-loss, one-off BBI/score weakness, peak giveback, ATR chandelier, and time efficiency are soft signals: industry main-fund inflow or a shrinking-volume pullback keeps the position on hold; an ordinary soft signal must appear in two distinct check windows before reducing half; industry outflow together with projected high-volume selling can confirm the half reduction immediately. Missing, failed, or stale flow and volume data stays neutral, and the remaining trend position still waits for a structural exit.

Industry direction reuses the Eastmoney industry main-net-flow snapshot already fetched by each scan. The projected stock-volume ratio combines current cumulative volume, the median volume of the latest 20 completed sessions, and the Dashboard's existing same-time market-turnover/full-day estimate, without adding a separate market request. A model-generated `SELL` for Shaofu B1 is advisory only and is downgraded to `HOLD`; the local position state machine owns actual exits.

### 3.3 Li Daxiao Rule Group

This rule group references the policy, value, bottom-formation, contrarian-sentiment, and leverage-risk-control frameworks in [`li-daxiao-skill`](https://github.com/sherjy/li-daxiao-skill). It uses highly liquid blue chips, low-level stabilization, low turnover, contracting volume and low volatility, anti-chasing rules, and risky-security filters as executable proxy signals.

### 3.4 Sector Tide

Sector Tide builds one cross-sectional snapshot from the same liquid-stock universe before scoring any stock. It then applies hard gates in the order market regime → industry tide → within-sector stock strength. Industry strength is a mandatory gate rather than a score bonus.

Each scan reads the rolling latest Dragon-Tiger snapshot only when its date exactly matches the prior A-share trading day. Main-list net flow, all top-five buy/sell seats, and institution-seat net flow are used as confirmation; incomplete same-day data and older snapshots remain neutral, with no arbitrary-date fallback. The industry overlay is capped at ±2.5 points and the stock overlay at ±0.35, so their combined effect on the ten-point candidate score cannot exceed ±0.45. A stock that is absent from the list stays neutral. A missing snapshot or incomplete seat data falls back to the available main list or a neutral value. A positive overlay is suppressed when the stock is up more than 7% that day or sits over 1.5 ATR above EMA20, while negative risk evidence remains active. This is a candidate-ranking feature for historical validation, not proof of higher future win rates.

The scan also reads an overnight summary only when it is valid for the current A-share date and represents the latest fully closed US session. Overall US risk appetite contributes between -0.15 and +0.05 points, while explicit US-sector-to-A-share-industry mappings contribute up to ±0.15 more. After the first technical and industry pass, only the top five Sector Tide candidates receive a structured three-day company-news precheck. Positive, negative, and neutral labels contribute +0.15, -0.30, and zero points respectively; missing, stale, or unclassified results stay neutral. Dragon-Tiger, overnight-US, and company-news confirmation is capped together between -0.90 and +0.60 points. Positive evidence applies only when the A-share industry is already leading or improving and the stock is not extended, while negative evidence remains active. The structured news record is cached with the candidate and reused by model decisioning, avoiding a duplicate request in the same scan cycle.

- **Main-theme Leader**: available only in offensive or rotation regimes, requires a leading industry and a stock in the top 20% of its industry, and accepts only a breakout or a low-volume EMA20 pullback. Its 8% single-name limit is an absolute ceiling; dynamic risk determines the actual size.
- **Early Rotation**: requires an improving industry and a stock in its top 30%. It rejects a one-day gain above 7% and an extension above 1.5 ATR from EMA20. Its 6% limit is an absolute ceiling.
- **Freeze Recovery**: available only after defense has cleared, requires one of the first industries and stocks to recover, and exits if recovery is not confirmed by T+2. Its 4% limit is an absolute ceiling; the recovery risk budget determines the actual size.

In offensive/rotation/recovery regimes, per-trade NAV risk is budgeted at 0.30%/0.20%/0.10%, strategy open-stop risk at 1.50%/0.80%/0.30%, sector risk at 0.60%/0.40%/0.20%, total exposure at 45%/30%/15%, and sector exposure at 12%/10%/6%. Defensive regimes set all new-risk budgets to zero. Effective loss distance equals structural stop distance plus the larger of the trailing 60-day downside-gap p95 and 0.5 ATR, plus a 0.20% execution reserve; the smaller of risk-sized weight and the registered ceiling binds. Each industry remains limited to two names. Missing industry-flow data explicitly falls back to volume participation and is never interpreted as an inflow.

#### User Guide: Sizing and Exit Behavior

- Every model BUY/SELL action must specify a round lot in multiples of 100 shares. The execution layer neither invents a default size nor automatically shrinks an oversized order.
- A candidate must have a valid structural stop no farther than both 6% and 1.5 ATR. Missing downside-gap/ATR reserve data blocks the entry.
- The requested size is checked against the dynamic single-name cap, market-regime total exposure, same-industry count, industry exposure, per-trade risk, strategy open risk, and strategy-sector risk. Any breach rejects the entire order and records the reason in the decision log.
- A regime budget is Sector Tide's maximum permission. If global market guidance, `DASHBOARD_MAX_TOTAL_POSITION_PCT`, or the cash reserve is tighter, the execution layer uses the smallest limit; a looser global setting never expands the Sector Tide budget.
- Total and industry exposure use the whole simulated account. Same-industry counts include every open position carrying that industry label. Open-stop and sector-stop risk totals include Sector Tide positions only.
- Main-theme Leader exits after five trading days without progress, Early Rotation after three days without continuation, and Freeze Recovery at T+2 without confirmation. These time-box rules are evaluated during the configured end-of-day exit check.
- `2R target = average cost + 2 × (average cost - entry structural stop)`. The first 2R event sells half; the remainder exits at `highest price since entry - 2 × ATR` after that trailing line is above cost.

#### Developer Contract: Regimes, Scores, and Data

The market regime is created from the same scan snapshot. The market composite weights core-index trend at 25%, advance/decline breadth at 25%, median return at 15%, limit-up/limit-down structure at 15%, the universe's 20-day trend at 10%, and volume participation at 10%.

| State | Rule |
|---|---|
| `offensive` | Market score ≥65 and advance breadth ≥55%, after state confirmation |
| `rotation` | Neither defensive nor offensive, after state confirmation |
| `recovery` | The prior confirmed state was defensive and the current raw state has cleared defense |
| `defensive` | Compound hard stop, or market score <40; all new-risk budgets become zero |

The compound hard stop requires index breakdown, market-breadth breakdown, and limit-down expansion at the same time. Except for a hard stop, a state change normally requires two consecutive scans with the same raw state; a first run with no state history accepts the current result immediately.

Industry score weights and tide thresholds are:

| Factor | Weight |
|---|---:|
| 20-day relative-strength percentile | 25% |
| 5-day relative-strength percentile | 15% |
| Rank-acceleration percentile | 15% |
| Breadth above EMA20 | 20% |
| 20-day new-high ratio | 10% |
| Industry flow; volume participation when missing | 10% |
| Industry turnover-liquidity percentile | 5% |
| Prior-trading-day Dragon-Tiger confirmation | Capped ±2.5-point overlay outside the base score |
| Latest completed US session and industry mapping | Capped -0.25 to +0.20 stock-score overlay |
| Top-five candidate company news over the prior three days | +0.15 positive / -0.30 negative stock-score overlay |

An industry needs at least three valid members, and each stock needs at least 55 daily bars. `leading` requires an industry score ≥75 and a 20-day relative-strength percentile ≥70. `improving` requires a score ≥65, rank acceleration ≥15, and a 5-day relative-strength percentile ≥65. A score <45 or 20-day relative-strength percentile <35 is `lagging`; every other case is `weakening`.

The entry-score thresholds for Main-theme Leader, Early Rotation, and Freeze Recovery are 8.0, 8.2, and 8.5; their minimum within-industry strength percentiles are 80, 70, and 70. Candidate-cache records must carry the market regime, industry tide, Dragon-Tiger snapshot date/availability/capped adjustment, US-session date/risk tone/sector mapping, news label/summary/fetch time, structural stop, gap reserve, effective loss distance, and all applicable risk budgets. The execution layer recalculates risk using the live simulated fill price and shared registered parameters; it does not trust model-supplied risk numbers.

The primary implementation files are `app/strategies/scoring/sector_tide.py`, `app/strategies/sector_tide_risk.py`, and `app/trading/practice_trader.py`. Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_sector_tide_strategy.py' -v` for the focused regression suite and `./scripts/validate.sh` for full validation.

### 3.5 NiuOne Method

The NiuOne Method first scores every stock in the same full, quote-valid cross section, then aggregates strong stocks by industry as an auditable theme proxy. Stock strength combines 20-day and 5-day relative strength, volume participation, relative turnover amount, EMA20/EMA50 alignment, and 20-day highs. Turnover amount is a capital-participation signal only: it is neither an eligibility gate nor an input to position caps. Industry is a deterministic proxy, not an assertion that similarly named stocks share a concept.

Theme scores combine strong-stock strength, breadth, leadership depth, capital participation, persistence across market dates, and bounded Dragon-Tiger/news confirmation. Contribution weights use stock strength and square-root turnover; effective strong-stock count is `1 / Σ(weight²)`. A single dominant stock is penalized and cannot confirm a mainline. The Theme Strength page also shows normalized effective breadth as `effective strong-stock count / valid theme members × 100%` for comparison across differently sized themes, while state thresholds continue to use the raw effective count. The result may explicitly be `mainline.mode=none`.

Theme states progress through `candidate`, `emerging`, `mainline`, `diverging`, `fading`, and `inactive`, but scan observation is now separated from trading confirmation. No number of same-day scans can authorize a mainline buy: a theme that meets the cross-sectional threshold is recorded as an `intraday_mainline` observation only. It becomes `mainline` only when it remains strong on the next adjacent trading day and at least two of its top-five core stocks persist. A date gap or a completely replaced core resets confirmation. Mainline quality requires score ≥75, at least three strong stocks, and effective count ≥2.4.

- **NiuOne Leader** requires a cross-trading-day confirmed mainline and a top-20% core stock with a breakout or first low-volume EMA20 pullback. It rejects gains above 4% or extensions above 1 ATR. Threshold 8.0; absolute cap 30%.
- **NiuOne Pullback** requires a mainline/diverging theme with score ≥70 and a top-30% stock reclaiming or holding EMA20. It rejects gains above 4% or extensions above 1 ATR. Threshold 8.2; cap 25%.
- **NiuOne Emerging** requires an emerging theme that persisted across adjacent trading days with at least two continuing core stocks and a core breakout/reclaim. Purely intraday strength cannot trigger it. It rejects gains above 7% or extensions above 1.5 ATR. Threshold 8.4; cap 15%, with no add before mainline confirmation.

Offensive/rotation/recovery per-trade NAV risk is 1.50%/1.00%/0.60%; suite open-risk limits are 4.50%/3.00%/1.80%, theme-risk limits 3.00%/2.00%/1.20%, total exposure 70%/55%/35%, and theme exposure 55%/40%/25%. Defensive state sets them to zero. The suite has a hard five-position limit, while market and pre-lunch dynamic controls normally tighten it to three. Structural-stop, gap, execution, two-name-per-theme, 2R partial-exit, and 2 ATR trailing checks are enforced locally. Dragon-Tiger and three-day news are bounded confirmation only: positive context cannot manufacture a mainline or override an entry blocker, while explicit negative evidence remains active. Primary files are `app/strategies/scoring/niuone.py`, `app/strategies/niuone_risk.py`, and `app/trading/practice_trader.py`. Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_niuone_strategy.py' -v` for focused regressions and `./scripts/validate.sh` for full validation.

The Dashboard's “Theme Strength” section at `/niuone-mainline` is a full-market theme-research view independent of the configured trading universe. It shows the market regime, cross-day confirmed mainline, intraday observation mainline, the five highest-scoring themes, and continuing core stocks. Coverage is calculated as stocks valid for theme clustering divided by the full reference pool; the page does not expose trade candidates, position limits, or BUY status. Uncovered stocks are assigned to mutually exclusive processing-stage reasons: unavailable or fewer than 30 daily bars, fewer than the required 55 bars, invalid key metrics, missing industry mapping, or other incomplete data. Legacy snapshots without this detail explicitly show that the next scan must fill it in. Every NiuOne scan atomically writes a dedicated `niuone_mainline_latest.json` state cache. That state remains available to the page and the next NiuOne scan even after another active strategy replaces the shared candidate cache. The five-theme limit applies only to the public page; the strategy cache retains every theme for cross-day continuity checks. Browsers receive only an allow-listed public view rather than the raw scan context.

The names above are used only to label rule experiments in this project. They do not indicate that the original authors participated in, approved, or endorsed this project. When redistributing related descriptions, retain the references to `zettaranc-skill` and `li-daxiao-skill`.

## 4. Simulated Decision Intelligence Package

The simulation process can compress multiple sources into a structured context and save it with the decision log. By default, it may include:

- Market-monitoring guidance and overnight market summaries;
- A-share indices, A50, U.S. indices or futures, gold, crude oil, and other market data;
- Sector performance, industry capital flows, trading activity, and turnover information;
- Recent news about candidate samples and confirmation or divergence between industry and market data;
- Simulated-account cash, total exposure, position weights, profit-and-loss status, and rule markers.

Each practice-trading candidate scan reuses real-time quotes already retrieved for the configured stock universe, then recalculates the market label from the current counts of advancing and declining stocks and the breadth of limit-up and limit-down stocks. If data coverage is insufficient, the snapshot is stale, or the market is still in the 9:25 opening-auction phase, the system falls back to the latest auction, midday, or post-close report. Even if the current scan finds no candidates, it still refreshes and records the market context.

Position weight is calculated as `price × quantity ÷ current simulated total equity`. The log records each change as a percentage of simulated total equity, as well as the resulting individual-position and total-position percentages.

Related settings:

| Setting | Description |
|---|---|
| `DASHBOARD_DECISION_INTELLIGENCE_ENABLED` | Whether to enable the structured intelligence package |
| `DASHBOARD_DECISION_INTELLIGENCE_TTL_SECONDS` | Cache lifetime for aggregated data |
| `DASHBOARD_DECISION_INTELLIGENCE_MAX_ITEMS` | Maximum number of items in each information category |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Custom simulation-discipline text |
| `DASHBOARD_MAX_OPEN_POSITIONS` | Reference maximum number of simulated open positions |
| `DASHBOARD_MAX_NEW_BUYS_PER_DECISION` | Reference maximum number of new simulated samples per decision round |
| `DASHBOARD_MAX_SINGLE_POSITION_PCT` | Reference percentage for an individual simulated position |
| `DASHBOARD_MAX_TOTAL_POSITION_PCT` | Reference percentage for total simulated exposure |
| `DASHBOARD_MIN_CASH_RESERVE_PCT` | Reference percentage for simulated cash reserves |

Percentage settings are primarily model context and research discipline by default. Suites with registered hard limits recheck their own limits in the simulation layer. NiuOne single-name exposure is governed by its independent risk budget and registered 30%/25%/15% caps, rather than being reduced to the default 10% single-name reference. Global total exposure, dynamic market exposure, and cash-reserve limits still apply whenever they are stricter. These controls must not be treated as safeguards for a real brokerage account.

## 5. Configuration

Prefer maintaining the active independent strategy, text rules, and simulation discipline on the dashboard settings page. The corresponding environment variables include:

| Setting | Description |
|---|---|
| `DASHBOARD_ACTIVE_STRATEGY` | Active independent strategy: `base`, `zettaranc`, `li_daxiao_bottom`, `sector_tide`, `niuone`, or `preset_text` |
| `DASHBOARD_PRESET_STRATEGY_TEXT` | Custom preset text rules |
| `DASHBOARD_STOCK_UNIVERSE` | Final-candidate and new-BUY scope: `st`, `chi_next`, `star_market`, and `main_board`; defaults to Main Board; NiuOne's full reference universe does not expand it |
| `DASHBOARD_TRADE_DISCIPLINE_TEXT` | Additional simulation discipline |

When NiuOne is active, the scanner uses every supported non-ST Shanghai/Shenzhen A-share (Main Board, ChiNext, and STAR Market) as its market and mainline reference universe. It fetches the complete quote set and sends every reference stock into uncapped K-line analysis, with no prefilter based on turnover amount, daily return, or limit-up-like movement. Final displayed candidates and new BUYs remain strictly limited by `DASHBOARD_STOCK_UNIVERSE` and require only a usable simulated-execution price. Existing positions may still execute stop-loss, take-profit, and other SELL controls after falling outside the setting, so changing the universe cannot trap a position. ST names enter final trading only when explicitly selected. Beijing Stock Exchange names remain excluded because the current quote, board-limit, and trading-permission models do not support them yet.

Local configuration is stored in `.local-data/dashboard.env` by default. This file may contain model keys and administrative credentials and must not be committed to Git or copied into public contexts.

The legacy `DASHBOARD_STRATEGY_SOURCE` and `DASHBOARD_ENABLED_PERSONA_STRATEGIES` settings are read only for seamless migration when `DASHBOARD_ACTIVE_STRATEGY` is absent.

## 6. Extending Independent Strategies

Strategy code is centralized under `app/strategies/`:

- `registry.py` owns metadata, groups, aliases, enablement, and settings options.
- `scoring/` owns indicators, hard gates, individual scorers, and the multi-strategy comparison engine.
- `selection.py` and `policy.py` own candidate eligibility, strategy-aware display selection, and position policy.
- `attribution.py` and `performance.py` own strategy marks, attribution, and performance summaries.
- `exits.py` and `prompts.py` own strategy-specific exit rules and model-prompt fragments.

`app/screening/multi_strategy.py` owns market-data retrieval, full-market scan orchestration, and caching. `app/trading/practice_trader.py` owns account, risk-control, and simulated-execution orchestration. Legacy module-name adapters are centralized under `app/compat/`; new code should import from the `strategies` package.

To add a built-in strategy:

1. Add its `label`, `color`, `desc`, `scorer`, `profile`, `position_limit_pct`, and `aliases` in `app/strategies/registry.py`.
2. Implement `score_xxx(rows)` in the appropriate file under `app/strategies/scoring/` and register it in the explicit map in `scoring/__init__.py`.
3. If it has dedicated exit or decision semantics, update only `exits.py` or `prompts.py`; do not put those rules back into the scanner or trader.
4. Add automated tests for scoring boundaries, empty data, and abnormal market data.
5. Run `./scripts/validate.sh` to complete validation.

The scanner iterates over the enabled scorers and outputs `strategy_meta` to the dashboard and simulated-review module.

## 7. Usage Boundaries

- Do not treat model-generated content as factual. Verify the original data and information sources.
- Do not use this project as a substitute for licensed institutional services, professional risk assessment, or your own independent judgment.
- Historical replay, rule scores, and simulation results do not represent future performance.
- When adding data sources, verify data licensing, request-frequency limits, privacy requirements, and redistribution terms.
