from datetime import datetime, timezone

from execution.types import ExitPolicy, TradeRecord
from mt5.types import OrderSide


class ExitManager:
    def desired_protection(self, trade: TradeRecord, position: dict, current_price: float, point_size: float,
                           policy: ExitPolicy, now: datetime | None = None) -> tuple[float | None, float | None, str] | None:
        now = now or datetime.now(timezone.utc)
        entry, side = trade.order.stop_loss, trade.order.side
        existing_sl, tp = position.get("sl"), position.get("tp")
        if policy.max_holding_seconds and (now - trade.created_at).total_seconds() >= policy.max_holding_seconds:
            return None, None, "time_exit"
        if trade.order.side is OrderSide.BUY:
            move = current_price - (position.get("price_open") or current_price)
            proposed = existing_sl
            if policy.break_even_after_points and move / point_size >= policy.break_even_after_points: proposed = max(proposed or -float("inf"), position.get("price_open"))
            if policy.trailing_stop_points: proposed = max(proposed or -float("inf"), current_price - policy.trailing_stop_points * point_size)
        else:
            move = (position.get("price_open") or current_price) - current_price
            proposed = existing_sl
            if policy.break_even_after_points and move / point_size >= policy.break_even_after_points: proposed = min(proposed or float("inf"), position.get("price_open"))
            if policy.trailing_stop_points: proposed = min(proposed or float("inf"), current_price + policy.trailing_stop_points * point_size)
        if proposed != existing_sl: return proposed, tp, "protection_update"
        return None
