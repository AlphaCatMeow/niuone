"""A-share fee assumptions shared by paper trading and historical replay."""
from __future__ import annotations

from typing import Any


# The configured paper account is “万一免五”: 0.01% commission without a
# five-yuan minimum. Statutory sell-side stamp duty and bilateral transfer fee
# remain separate so callers can expose the exact assumption to users.
A_SHARE_COMMISSION_RATE = 0.0001
A_SHARE_MINIMUM_COMMISSION = 0.0
A_SHARE_SELL_STAMP_DUTY_RATE = 0.0005
A_SHARE_TRANSFER_FEE_RATE = 0.00001


def calculate_a_share_trade_fees(amount: Any, side: str) -> dict[str, float]:
    """Return rounded paper-account fees for one A-share trade."""
    gross = max(0.0, float(amount or 0.0))
    commission = max(
        gross * A_SHARE_COMMISSION_RATE,
        A_SHARE_MINIMUM_COMMISSION,
    )
    transfer_fee = gross * A_SHARE_TRANSFER_FEE_RATE
    stamp_duty = (
        gross * A_SHARE_SELL_STAMP_DUTY_RATE
        if str(side or "").upper() == "SELL" else 0.0
    )
    return {
        "commission": round(commission, 2),
        "transfer_fee": round(transfer_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "total_fee": round(commission + transfer_fee + stamp_duty, 2),
    }


__all__ = [
    "A_SHARE_COMMISSION_RATE",
    "A_SHARE_MINIMUM_COMMISSION",
    "A_SHARE_SELL_STAMP_DUTY_RATE",
    "A_SHARE_TRANSFER_FEE_RATE",
    "calculate_a_share_trade_fees",
]
