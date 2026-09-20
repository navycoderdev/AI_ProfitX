from execution.types import TradeRecord


class ExecutionReconciler:
    """Reconciles a local trade by unique magic/comment ownership before and after all MT5 operations."""
    def reconcile(self, trade: TradeRecord, positions: list[dict]) -> dict | None:
        candidates = [position for position in positions if position.get("magic") == trade.owner.magic_number and
                      (position.get("comment") == trade.trade_id or position.get("ticket") == trade.position_id)]
        if not candidates: return None
        position = candidates[0]
        trade.position_id = position.get("ticket", trade.position_id)
        trade.filled_volume = float(position.get("volume", trade.filled_volume))
        trade.add_telemetry("reconciled_position", {"position": position})
        return position
