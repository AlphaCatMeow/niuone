#!/usr/bin/env python3
"""实战页面 · SQLite 数据库层

替代 JSON 文件存储，提供：
  - daily_equity 每日资金快照
  - position_snapshots 每日持仓快照
  - trades 交易记录
  - decisions 决策记录
  - 首次运行自动从 JSON 迁移历史数据
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from a_share_calendar import is_a_share_trading_day
from niuone_paths import get_dashboard_home

DASHBOARD_HOME = get_dashboard_home(Path(__file__).resolve().parents[1])
DB_PATH = Path(os.environ.get("DASHBOARD_NIUNIU_DB", DASHBOARD_HOME / "niuniu.db")).expanduser()
STATE_FILE = Path(
    os.environ.get(
        "DASHBOARD_PORTFOLIO_STATE",
        DASHBOARD_HOME / "cron" / "output" / "niuniu_practice_portfolio.json",
    )
).expanduser()

ACCOUNT_HISTORY_KINDS = frozenset({
    "trade_log",
    "decision_log",
    "equity_history",
    "daily_equity_history",
})


def _json_safe(value):
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_payload(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decision_event_key(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _history_logical_key(kind: str, value: Any, payload_json: str) -> str:
    """Return the active-record identity without discarding archived revisions."""
    if isinstance(value, Mapping):
        if kind in {"equity_history", "daily_equity_history"}:
            time_text = str(value.get("time") or value.get("date") or "")
            if time_text:
                return f"time:{time_text}"
        if kind == "trade_log":
            identity = {
                field: value.get(field, "")
                for field in (
                    "time",
                    "action",
                    "code",
                    "shares",
                    "price",
                    "reason",
                )
            }
            return "trade:" + hashlib.sha256(
                _canonical_payload(identity).encode("utf-8")
            ).hexdigest()
    return "payload:" + _decision_event_key(payload_json)


def _history_event_time(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("time") or value.get("date") or "")


def _is_trading_day_text(value: str) -> bool:
    try:
        return is_a_share_trading_day(datetime.strptime(str(value or "")[:10], "%Y-%m-%d"))
    except Exception:
        return True


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    """初始化数据库表结构。"""
    conn = _connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS daily_equity (
        date       TEXT PRIMARY KEY,   -- 'YYYY-MM-DD'
        equity     REAL NOT NULL,      -- 总权益
        cash       REAL NOT NULL,      -- 现金
        market_value REAL NOT NULL,    -- 持仓市值
        pnl_pct    REAL NOT NULL,      -- 累计收益率%
        account_created_at TEXT NOT NULL DEFAULT '', -- 账户会话创建时间
        created_at TEXT NOT NULL       -- 'YYYY-MM-DD HH:MM:SS'
    );
    
    CREATE TABLE IF NOT EXISTS position_snapshots (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        date       TEXT NOT NULL,      -- 'YYYY-MM-DD'
        code       TEXT NOT NULL,      -- 股票代码
        name       TEXT DEFAULT '',
        shares     INTEGER NOT NULL,
        avg_cost   REAL NOT NULL,
        last_price REAL NOT NULL,
        market_value REAL NOT NULL,
        pnl        REAL NOT NULL,
        pnl_pct    REAL NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(date, code)
    );
    
    CREATE TABLE IF NOT EXISTS trades (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        time       TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM:SS'
        action     TEXT NOT NULL,      -- 'BUY' | 'SELL'
        code       TEXT NOT NULL,
        name       TEXT DEFAULT '',
        shares     INTEGER NOT NULL,
        price      REAL NOT NULL,
        amount     REAL NOT NULL,
        commission REAL DEFAULT 0,
        transfer_fee REAL DEFAULT 0,
        stamp_duty REAL DEFAULT 0,
        pnl        REAL,               -- SELL时才有的盈亏
        reason     TEXT DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '', -- 完整成交证据，供严格前向评估
        created_at TEXT NOT NULL
    );
    
    CREATE TABLE IF NOT EXISTS decisions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        time       TEXT NOT NULL,
        model      TEXT DEFAULT '',
        provider   TEXT DEFAULT '',
        trade_allowed INTEGER DEFAULT 1,
        trade_reason TEXT DEFAULT '',
        summary    TEXT DEFAULT '',
        actions_json TEXT DEFAULT '',   -- JSON array of actions
        error      TEXT DEFAULT '',
        b1_generated_at TEXT DEFAULT '',
        schedule_slot TEXT DEFAULT '',
        schedule_run_kind TEXT DEFAULT '',
        event_key TEXT,
        payload_json TEXT NOT NULL DEFAULT '', -- 完整候选与决策证据
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS account_history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        history_kind TEXT NOT NULL,
        event_key    TEXT NOT NULL,
        logical_key  TEXT NOT NULL,
        event_time   TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL,
        archived_at  TEXT NOT NULL,
        UNIQUE(history_kind, event_key)
    );
    
    CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);
    CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
    CREATE INDEX IF NOT EXISTS idx_positions_date ON position_snapshots(date);
    CREATE INDEX IF NOT EXISTS idx_daily_equity_date ON daily_equity(date);
    CREATE INDEX IF NOT EXISTS idx_account_history_kind_time
        ON account_history(history_kind, event_time, id);
    CREATE INDEX IF NOT EXISTS idx_account_history_kind_logical
        ON account_history(history_kind, logical_key, id);
    CREATE TRIGGER IF NOT EXISTS account_history_no_update
        BEFORE UPDATE ON account_history
        BEGIN
            SELECT RAISE(ABORT, 'account_history is append-only');
        END;
    CREATE TRIGGER IF NOT EXISTS account_history_no_delete
        BEFORE DELETE ON account_history
        BEGIN
            SELECT RAISE(ABORT, 'account_history is append-only');
        END;
    """)
    _ensure_trade_payload_column(conn)
    _ensure_decision_evidence_columns(conn)
    _ensure_daily_equity_evidence_columns(conn)
    _deduplicate_trades(conn)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_unique_event
        ON trades(time, action, code, shares, price, amount, reason)
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_unique_event
        ON decisions(event_key)
        WHERE event_key IS NOT NULL AND event_key <> ''
    """)
    conn.commit()
    conn.close()


def _ensure_trade_payload_column(conn: sqlite3.Connection):
    """Add the lossless trade payload to upgraded databases without rewriting rows."""
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    if "payload_json" not in columns:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN payload_json TEXT NOT NULL DEFAULT ''"
        )


def _ensure_decision_evidence_columns(conn: sqlite3.Connection):
    """Add durable decision evidence without rewriting legacy rows."""
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(decisions)").fetchall()
    }
    additions = {
        "b1_generated_at": "TEXT DEFAULT ''",
        "schedule_slot": "TEXT DEFAULT ''",
        "schedule_run_kind": "TEXT DEFAULT ''",
        "event_key": "TEXT",
        "payload_json": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE decisions ADD COLUMN {name} {definition}"
            )


def _ensure_daily_equity_evidence_columns(conn: sqlite3.Connection):
    """Add account-session continuity without rewriting historical marks."""
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(daily_equity)").fetchall()
    }
    if "account_created_at" not in columns:
        conn.execute(
            "ALTER TABLE daily_equity ADD COLUMN account_created_at "
            "TEXT NOT NULL DEFAULT ''"
        )


def _deduplicate_trades(conn: sqlite3.Connection):
    """Keep one row per simulated trade event before enforcing uniqueness."""
    conn.execute("UPDATE trades SET reason = '' WHERE reason IS NULL")
    conn.execute("""
        DELETE FROM trades
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM trades
            GROUP BY time, action, code, shares, price, amount, reason
        )
    """)


def _archive_account_history_conn(
    conn: sqlite3.Connection,
    state: Mapping[str, Any],
) -> int:
    """Append lossless history payloads inside the caller's transaction."""
    archived_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[tuple[str, str, str, str, str, str]] = []
    for kind in ACCOUNT_HISTORY_KINDS:
        values = state.get(kind)
        if not isinstance(values, list):
            continue
        for value in values:
            payload_json = _canonical_payload(value)
            event_key = _decision_event_key(payload_json)
            rows.append((
                kind,
                event_key,
                _history_logical_key(kind, value, payload_json),
                _history_event_time(value),
                payload_json,
                archived_at,
            ))
    if rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO account_history (
                history_kind, event_key, logical_key, event_time,
                payload_json, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def archive_account_history(state: Mapping[str, Any]) -> bool:
    """Atomically append all JSON account history before the file is compacted."""
    conn = None
    try:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        _archive_account_history_conn(conn, state)
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except sqlite3.Error:
                pass
        print(
            "[niuniu_db] 归档账户历史失败: "
            f"{type(exc).__name__}",
        )
        return False


def archive_state_file_history() -> bool:
    """Idempotently seed the immutable archive from a legacy full-state JSON."""
    if not STATE_FILE.exists():
        return True
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            "[niuniu_db] 读取账户历史 JSON 失败: "
            f"{type(exc).__name__}",
        )
        return False
    if not isinstance(state, Mapping):
        return False
    return archive_account_history(state)


def query_account_history(kind: str, limit: int | None = None) -> list[Any]:
    """Read active history rows while keeping every older revision in SQLite."""
    if kind not in ACCOUNT_HISTORY_KINDS:
        raise ValueError(f"unsupported account history kind: {kind}")
    conn = None
    try:
        conn = _connect()
        params: list[Any] = [kind]
        active_rows_sql = """
            SELECT h.id, h.event_time, h.payload_json
            FROM account_history AS h
            WHERE h.history_kind = ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM account_history AS newer
                  WHERE newer.history_kind = h.history_kind
                    AND newer.logical_key = h.logical_key
                    AND newer.id > h.id
              )
        """
        if limit is not None:
            resolved_limit = max(0, int(limit))
            if resolved_limit == 0:
                conn.close()
                return []
            query = f"""
                SELECT payload_json
                FROM ({active_rows_sql}
                      ORDER BY h.event_time DESC, h.id DESC
                      LIMIT ?)
                ORDER BY event_time, id
            """
            params.append(resolved_limit)
        else:
            query = f"""
                SELECT payload_json
                FROM ({active_rows_sql})
                ORDER BY event_time, id
            """
        raw_rows = conn.execute(query, params).fetchall()
        conn.close()
        restored: list[Any] = []
        invalid_count = 0
        for (payload_json,) in raw_rows:
            try:
                restored.append(json.loads(payload_json))
            except (TypeError, json.JSONDecodeError):
                invalid_count += 1
        if invalid_count:
            print(
                "[niuniu_db] 跳过无法解析的账户历史记录: "
                f"{invalid_count} 条"
            )
        return restored
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        print(
            "[niuniu_db] 查询账户历史失败: "
            f"{type(exc).__name__}",
        )
        return []


def migrate_from_json():
    """从 niuniu_practice_portfolio.json 迁移历史数据到 SQLite。"""
    json_path = STATE_FILE
    if not json_path.exists():
        return
    
    conn = _connect()
    try:
        state = json.loads(json_path.read_text(encoding="utf-8"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Archive the source payloads first. The structured summary tables below
        # remain useful for existing reports, while account_history is the
        # lossless, append-only source used before compacting the JSON state.
        _archive_account_history_conn(conn, state)
        
        # 1. 迁移每日资金快照
        daily_history = state.get("daily_equity_history", [])
        if daily_history:
            migrated = 0
            for pt in daily_history:
                date = pt.get("time", "")[:10]
                if not date:
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO daily_equity (date, equity, cash, market_value, pnl_pct, account_created_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (date, pt.get("equity", 0), pt.get("cash", 0), pt.get("market_value", 0), pt.get("pnl_pct", 0), state.get("created_at", ""), pt.get("time", now)))
                migrated += 1
            print(f"[niuniu_db] 迁移 daily_equity: {migrated} 条")
        
        # 2. 迁移交易日志
        trade_log = state.get("trade_log", [])
        if trade_log:
            migrated = 0
            for t in trade_log:
                action = t.get("action", "")
                if not action:
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO trades (time, action, code, name, shares, price, amount, commission, transfer_fee, stamp_duty, pnl, reason, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    t.get("time", now), action, t.get("code", ""), t.get("name", ""),
                    t.get("shares", 0), t.get("price", 0), t.get("amount", 0),
                    t.get("commission", 0), t.get("transfer_fee", 0), t.get("stamp_duty", 0),
                    t.get("pnl"), t.get("reason", ""),
                    json.dumps(t, ensure_ascii=False, sort_keys=True), t.get("time", now)
                ))
                migrated += 1
            print(f"[niuniu_db] 迁移 trades: {migrated} 条")
        
        # 3. 迁移决策日志
        decision_log = state.get("decision_log", [])
        if decision_log:
            migrated = 0
            for d in decision_log:
                dec = d.get("decision", {})
                payload_json = _canonical_payload(d)
                conn.execute("""
                    INSERT OR IGNORE INTO decisions (
                        time, model, provider, trade_allowed, trade_reason,
                        summary, actions_json, error, b1_generated_at,
                        schedule_slot, schedule_run_kind, event_key,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    d.get("time", now), dec.get("model", ""), dec.get("provider", ""),
                    int(d.get("trade_allowed", True)), d.get("trade_reason", ""),
                    dec.get("summary", ""), json.dumps(dec.get("actions", []), ensure_ascii=False),
                    dec.get("error", ""), d.get("b1_generated_at", ""),
                    d.get("schedule_slot", ""), d.get("schedule_run_kind", ""),
                    _decision_event_key(payload_json), payload_json,
                    d.get("time", now),
                ))
                migrated += 1
            print(f"[niuniu_db] 迁移 decisions: {migrated} 条")
        
        # 4. 当前持仓快照
        positions = state.get("positions", {})
        if positions:
            today = datetime.now().strftime("%Y-%m-%d")
            migrated = 0
            for code, p in positions.items():
                qty = int(p.get("qty") or p.get("shares") or 0)
                avg_cost = float(p.get("avg_cost", 0))
                last_price = float(p.get("last_price", avg_cost))
                mv = last_price * qty
                pnl = (last_price - avg_cost) * qty
                pnl_pct_val = ((last_price / avg_cost - 1) * 100) if avg_cost > 0 else 0
                conn.execute("""
                    INSERT OR IGNORE INTO position_snapshots (date, code, name, shares, avg_cost, last_price, market_value, pnl, pnl_pct, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (today, code, p.get("name", ""), qty, avg_cost, last_price, mv, pnl, pnl_pct_val, now))
                migrated += 1
            print(f"[niuniu_db] 迁移 positions: {migrated} 条")
        
        conn.commit()
        print("[niuniu_db] 迁移完成")
    except Exception as e:
        conn.rollback()
        print(f"[niuniu_db] 迁移失败: {e}")
    finally:
        conn.close()


def record_daily_equity(pt: dict):
    """记录每日资金快照到 DB。pt 包含 time, equity, cash, market_value, pnl_pct。"""
    try:
        conn = _connect()
        date = pt.get("time", "")[:10]
        if not _is_trading_day_text(date):
            conn.close()
            return
        conn.execute("""
            INSERT OR REPLACE INTO daily_equity (date, equity, cash, market_value, pnl_pct, account_created_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (date, pt.get("equity", 0), pt.get("cash", 0), pt.get("market_value", 0), pt.get("pnl_pct", 0), pt.get("account_created_at", ""), pt.get("time", "")))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[niuniu_db] 写入 daily_equity 失败: {e}")


def record_trade(t: dict) -> bool:
    """记录单笔交易到 DB。"""
    conn = None
    try:
        conn = _connect()
        payload_json = _canonical_payload(t)
        conn.execute("""
            INSERT OR IGNORE INTO trades (time, action, code, name, shares, price, amount, commission, transfer_fee, stamp_duty, pnl, reason, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t.get("time", ""), t.get("action", ""), t.get("code", ""), t.get("name", ""),
            t.get("shares", 0), t.get("price", 0), t.get("amount", 0),
            t.get("commission", 0), t.get("transfer_fee", 0), t.get("stamp_duty", 0),
            t.get("pnl"), t.get("reason", ""), payload_json, t.get("time", "")
        ))
        conn.execute("""
            UPDATE trades
            SET payload_json = ?
            WHERE time = ? AND action = ? AND code = ? AND shares = ?
              AND price = ? AND amount = ? AND reason = ?
              AND payload_json = ''
        """, (
            payload_json,
            t.get("time", ""), t.get("action", ""), t.get("code", ""),
            t.get("shares", 0), t.get("price", 0), t.get("amount", 0),
            t.get("reason", ""),
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except sqlite3.Error:
                pass
        print(f"[niuniu_db] 写入 trade 失败: {type(e).__name__}")
        return False


def record_decision(d: dict) -> bool:
    """记录单条决策到 DB。"""
    conn = None
    try:
        conn = _connect()
        dec = d.get("decision", {})
        payload_json = _canonical_payload(d)
        event_key = _decision_event_key(payload_json)
        conn.execute("""
            INSERT OR IGNORE INTO decisions (
                time, model, provider, trade_allowed, trade_reason,
                summary, actions_json, error, b1_generated_at,
                schedule_slot, schedule_run_kind, event_key,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d.get("time", ""), dec.get("model", ""), dec.get("provider", ""),
            int(d.get("trade_allowed", True)), d.get("trade_reason", ""),
            dec.get("summary", ""), json.dumps(dec.get("actions", []), ensure_ascii=False),
            dec.get("error", ""), d.get("b1_generated_at", ""),
            d.get("schedule_slot", ""), d.get("schedule_run_kind", ""),
            event_key, payload_json, d.get("time", ""),
        ))
        conn.commit()
        persisted = conn.execute(
            "SELECT 1 FROM decisions WHERE event_key = ? LIMIT 1",
            (event_key,),
        ).fetchone()
        conn.close()
        return persisted is not None
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except sqlite3.Error:
                pass
        print(f"[niuniu_db] 写入 decision 失败: {type(e).__name__}")
        return False


def snapshot_positions(positions: dict):
    """保存当前持仓快照到 DB。"""
    try:
        conn = _connect()
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("DELETE FROM position_snapshots WHERE date = ?", (today,))
        for code, p in positions.items():
            qty = int(p.get("qty") or p.get("shares") or 0)
            if qty <= 0:
                continue
            avg_cost = float(p.get("avg_cost", 0))
            last_price = float(p.get("last_price", avg_cost))
            mv = last_price * qty
            pnl = (last_price - avg_cost) * qty
            pnl_pct_val = ((last_price / avg_cost - 1) * 100) if avg_cost > 0 else 0
            conn.execute("""
                INSERT OR REPLACE INTO position_snapshots (date, code, name, shares, avg_cost, last_price, market_value, pnl, pnl_pct, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (today, code, p.get("name", ""), qty, avg_cost, last_price, mv, pnl, pnl_pct_val, now))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[niuniu_db] 快照持仓失败: {e}")


def query_daily_equity() -> list[dict]:
    """查询每日资金快照，用于累计收益曲线。"""
    try:
        conn = _connect()
        cur = conn.execute("SELECT date, equity, cash, market_value, pnl_pct, account_created_at, created_at FROM daily_equity ORDER BY date")
        rows = [{"time": r[6] or (r[0] + " 15:00:00"), "date": r[0], "equity": r[1], "cash": r[2], "market_value": r[3], "pnl_pct": r[4], "account_created_at": r[5]} for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[niuniu_db] 查询 daily_equity 失败: {e}")
        return []


def has_daily_equity_table() -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_equity'").fetchone()
        return bool(row)
    finally:
        conn.close()


# ======== 自动初始化 ========
if not DB_PATH.exists() or DB_PATH.stat().st_size < 1024:
    init_db()
    migrate_from_json()
    archive_state_file_history()
elif not has_daily_equity_table():
    init_db()
    migrate_from_json()
    archive_state_file_history()
else:
    init_db()
    archive_state_file_history()
