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


def _canonical_payload(value: Mapping) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decision_event_key(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _is_trading_day_text(value: str) -> bool:
    try:
        return is_a_share_trading_day(datetime.strptime(str(value or "")[:10], "%Y-%m-%d"))
    except Exception:
        return True


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
    
    CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);
    CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
    CREATE INDEX IF NOT EXISTS idx_positions_date ON position_snapshots(date);
    CREATE INDEX IF NOT EXISTS idx_daily_equity_date ON daily_equity(date);
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


def migrate_from_json():
    """从 niuniu_practice_portfolio.json 迁移历史数据到 SQLite。"""
    json_path = STATE_FILE
    if not json_path.exists():
        return
    
    conn = _connect()
    try:
        state = json.loads(json_path.read_text())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. 迁移每日资金快照
        daily_history = state.get("daily_equity_history", [])
        if daily_history:
            migrated = 0
            for pt in daily_history:
                date = pt.get("time", "")[:10]
                if not date:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO daily_equity (date, equity, cash, market_value, pnl_pct, account_created_at, created_at)
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
                    INSERT OR REPLACE INTO position_snapshots (date, code, name, shares, avg_cost, last_price, market_value, pnl, pnl_pct, created_at)
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
elif not has_daily_equity_table():
    init_db()
    migrate_from_json()
else:
    init_db()
