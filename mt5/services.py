from dataclasses import replace
from datetime import datetime, timezone
from math import isclose
from threading import RLock
from typing import Any, Callable

from config.settings import Settings
from core.context import TradingContext
from core.exceptions import GatewayUnavailableError, ModeViolationError, RiskRejectedError
from database.audit import AuditRepository
from mt5.connection import MT5ConnectionManager, _mapping
from mt5.types import ExecutionResult, MarketOrder, OrderSide
from risk.types import RiskDecision


class _MT5Service:
    def __init__(self, manager: MT5ConnectionManager, settings: Settings, audit: AuditRepository) -> None:
        self.manager, self.settings, self.audit = manager, settings, audit

    @property
    def mt5(self):
        if not self.manager.connected:
            raise GatewayUnavailableError("MT5 is disconnected.")
        return self.manager.client


class MT5AccountService(_MT5Service):
    def snapshot(self) -> dict:
        data = _mapping(self.mt5.account_info())
        if not data:
            raise GatewayUnavailableError(f"MT5 account_info failed: {self.manager.last_error()}")
        selected = {key: data.get(key) for key in (
            "login", "balance", "equity", "margin", "margin_free", "margin_level", "leverage", "currency")}
        self.audit.write("mt5.account.snapshot", self.settings.app_env.value, selected)
        return selected


class MT5SymbolService(_MT5Service):
    def info(self, symbol: str) -> dict:
        result = _mapping(self.mt5.symbol_info(symbol))
        if not result:
            raise ValueError(f"Unknown MT5 symbol: {symbol}")
        return result

    def ensure_available(self, symbol: str) -> dict:
        info = self.info(symbol)
        if not info.get("visible", False) and not self.mt5.symbol_select(symbol, True):
            raise ValueError(f"Cannot select symbol: {symbol}")
        if info.get("trade_mode", 0) == getattr(self.mt5, "SYMBOL_TRADE_MODE_DISABLED", -1):
            raise ValueError(f"Symbol trading is disabled: {symbol}")
        return self.info(symbol)

    def tick(self, symbol: str) -> dict:
        self.ensure_available(symbol)
        tick = _mapping(self.mt5.symbol_info_tick(symbol))
        if not tick:
            raise GatewayUnavailableError(f"No live tick for {symbol}")
        return tick

    def supported_timeframes(self) -> dict[str, int]:
        return {name.removeprefix("TIMEFRAME_"): value for name, value in vars(self.mt5).items()
                if name.startswith("TIMEFRAME_") and isinstance(value, int)}


class MT5MarketDataService(_MT5Service):
    def rates(self, symbol: str, timeframe: int, start: datetime, end: datetime) -> list[dict]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("MT5 rate timestamps must be timezone-aware UTC.")
        raw = self.mt5.copy_rates_range(symbol, timeframe, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        if raw is None:
            raise GatewayUnavailableError(f"copy_rates_range failed: {self.manager.last_error()}")
        result = [_mapping(row) for row in raw]
        self.audit.write("mt5.market.rates", self.settings.app_env.value,
            {"symbol": symbol, "timeframe": timeframe, "count": len(result), "start": start.isoformat(), "end": end.isoformat()})
        return result

    def tick(self, symbol: str) -> dict:
        return MT5SymbolService(self.manager, self.settings, self.audit).tick(symbol)


class MT5PositionService(_MT5Service):
    def positions(self, symbol: str | None = None) -> list[dict]:
        rows = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        return [_mapping(row) for row in (rows or ())]

    def pending_orders(self, symbol: str | None = None) -> list[dict]:
        rows = self.mt5.orders_get(symbol=symbol) if symbol else self.mt5.orders_get()
        return [_mapping(row) for row in (rows or ())]

    def order_history(self, start: datetime, end: datetime) -> list[dict]:
        return [_mapping(row) for row in (self.mt5.history_orders_get(start, end) or ())]

    def deal_history(self, start: datetime, end: datetime, position: int | None = None) -> list[dict]:
        rows = self.mt5.history_deals_get(position=position) if position else self.mt5.history_deals_get(start, end)
        return [_mapping(row) for row in (rows or ())]


class MT5OrderService(_MT5Service):
    """Guarded order operations. Unknown outcomes reconcile; they never retry blindly."""
    def __init__(self, manager: MT5ConnectionManager, settings: Settings, audit: AuditRepository, order_gate: Callable[[], None] | None = None) -> None:
        super().__init__(manager, settings, audit)
        self._lock, self._inflight = RLock(), set()
        self._order_gate = order_gate

    def _ensure_live(self, context: TradingContext) -> None:
        if context.mode.value != "LIVE":
            raise ModeViolationError(f"Order blocked in {context.mode.value} mode.")
        self.settings.assert_mode(context.mode)

    def _validate(self, order: MarketOrder) -> tuple[dict, dict, float, int]:
        symbols = MT5SymbolService(self.manager, self.settings, self.audit)
        info, tick = symbols.ensure_available(order.symbol), symbols.tick(order.symbol)
        min_v, max_v, step = float(info["volume_min"]), float(info["volume_max"]), float(info["volume_step"])
        if not min_v <= order.volume <= max_v or not isclose((order.volume - min_v) / step, round((order.volume - min_v) / step), abs_tol=1e-7):
            raise ValueError("Requested volume violates symbol min/max/step.")
        bid, ask, point = float(tick["bid"]), float(tick["ask"]), float(info["point"])
        spread = round((ask - bid) / point)
        if spread > self.settings.mt5_max_spread_points:
            raise ValueError(f"Spread {spread} exceeds configured maximum.")
        price = ask if order.side is OrderSide.BUY else bid
        stops_level = float(info.get("trade_stops_level", 0)) * point
        for level in (order.stop_loss, order.take_profit):
            if level is not None and abs(price - level) < stops_level:
                raise ValueError("SL/TP violates symbol stop level.")
        margin = self.mt5.order_calc_margin(
            self.mt5.ORDER_TYPE_BUY if order.side is OrderSide.BUY else self.mt5.ORDER_TYPE_SELL,
            order.symbol, order.volume, price)
        account = _mapping(self.mt5.account_info())
        if margin is None or float(margin) > float(account.get("margin_free", 0)):
            raise ValueError("Insufficient available margin for requested order.")
        return info, tick, price, spread

    def _request(self, order: MarketOrder, price: float) -> dict:
        return {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": order.symbol, "volume": order.volume,
                "type": self.mt5.ORDER_TYPE_BUY if order.side is OrderSide.BUY else self.mt5.ORDER_TYPE_SELL,
                "price": price, "sl": order.stop_loss or 0.0, "tp": order.take_profit or 0.0,
                "deviation": order.deviation, "magic": order.magic or self.settings.mt5_magic_number,
                "comment": order.comment[:31], "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_IOC}

    def submit_market(self, context: TradingContext, order: MarketOrder, risk_decision: RiskDecision) -> ExecutionResult:
        if self._order_gate: self._order_gate()
        if not risk_decision.approved or risk_decision.position_size is None:
            raise RiskRejectedError(f"MT5 execution blocked by risk decision: {[code.value for code in risk_decision.reason_codes]}")
        self._ensure_live(context)
        order = replace(order, volume=risk_decision.position_size)
        fingerprint = order.client_order_id or f"{order.symbol}:{order.side}:{order.volume}:{order.magic}:{order.comment}"
        with self._lock:
            if fingerprint in self._inflight:
                raise ValueError("Duplicate order protection blocked an in-flight request.")
            self._inflight.add(fingerprint)
        try:
            _, _, price, spread = self._validate(order)
            request = self._request(order, price)
            check = self.mt5.order_check(request)
            check_data = _mapping(check)
            if not check or int(check_data.get("retcode", -1)) != 0:
                return ExecutionResult(False, True, check_data.get("retcode"), "MT5 order_check rejected request",
                    request_price=price, requested_volume=order.volume, spread_points=spread, raw=check_data)
            raw_result = self.mt5.order_send(request)
            result = self._normalise(raw_result, order, price, spread)
            self.audit.write("mt5.order.submit", self.settings.app_env.value,
                {"request": request, "result": result.audit_payload()}, str(context.session_id))
            if not result.final:
                return self.reconcile(order, result)
            return result
        finally:
            with self._lock:
                self._inflight.discard(fingerprint)

    def _normalise(self, raw: Any, order: MarketOrder, price: float, spread: int) -> ExecutionResult:
        data = _mapping(raw)
        retcode = data.get("retcode")
        done = {getattr(self.mt5, "TRADE_RETCODE_DONE", 10009), getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)}
        accepted = retcode in done
        # A non-successful result may represent a timeout or a transport failure.
        # Treat it as uncertain and reconcile with the terminal before deciding
        # anything; this service must never resend such an order automatically.
        final = accepted
        fill = data.get("price")
        return ExecutionResult(accepted, final, retcode, str(data.get("comment", "MT5 execution result")),
            mt5_ticket=data.get("order"), position_id=data.get("position"), deal_id=data.get("deal"),
            request_price=price, fill_price=fill, requested_volume=order.volume, filled_volume=data.get("volume"),
            spread_points=spread, slippage_points=((fill - price) if fill is not None else None),
            executed_at=datetime.now(timezone.utc), raw=data)

    def reconcile(self, order: MarketOrder, prior: ExecutionResult) -> ExecutionResult:
        """Inspect MT5 state; never submit a second order for an uncertain first attempt."""
        now = datetime.now(timezone.utc)
        positions = MT5PositionService(self.manager, self.settings, self.audit).positions(order.symbol)
        matches = [p for p in positions if p.get("magic") == (order.magic or self.settings.mt5_magic_number)]
        status = "reconciled open position" if matches else "uncertain execution; no retry submitted"
        matched_ticket = matches[0].get("ticket") if matches else None
        reconciled = ExecutionResult(bool(matches), True, prior.retcode, status,
            mt5_ticket=prior.mt5_ticket, position_id=matched_ticket, deal_id=prior.deal_id, request_price=prior.request_price,
            fill_price=prior.fill_price, requested_volume=prior.requested_volume,
            filled_volume=prior.filled_volume, spread_points=prior.spread_points,
            slippage_points=prior.slippage_points, executed_at=now, raw={"positions": matches, "prior": prior.raw})
        self.audit.write("mt5.order.reconcile", self.settings.app_env.value, reconciled.audit_payload())
        return reconciled

    def close_position(self, context: TradingContext, position: dict, deviation: int = 20) -> ExecutionResult:
        self._ensure_live(context)
        symbol, volume = position["symbol"], float(position["volume"])
        position_type = position["type"]
        tick = MT5SymbolService(self.manager, self.settings, self.audit).tick(symbol)
        is_buy_close = position_type == self.mt5.POSITION_TYPE_SELL
        price = float(tick["ask"] if is_buy_close else tick["bid"])
        request = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "position": position["ticket"],
                   "volume": volume, "type": self.mt5.ORDER_TYPE_BUY if is_buy_close else self.mt5.ORDER_TYPE_SELL,
                   "price": price, "deviation": deviation, "magic": self.settings.mt5_magic_number,
                   "comment": "platform-close", "type_time": self.mt5.ORDER_TIME_GTC,
                   "type_filling": self.mt5.ORDER_FILLING_IOC}
        raw = self.mt5.order_send(request)
        result = self._normalise(raw, MarketOrder(symbol, OrderSide.BUY if is_buy_close else OrderSide.SELL, volume), price, 0)
        self.audit.write("mt5.position.close", self.settings.app_env.value,
            {"position": position["ticket"], "result": result.audit_payload()}, str(context.session_id))
        return result

    def modify_position(self, context: TradingContext, ticket: int, symbol: str,
                        stop_loss: float | None, take_profit: float | None) -> ExecutionResult:
        self._ensure_live(context)
        info = MT5SymbolService(self.manager, self.settings, self.audit).info(symbol)
        tick = MT5SymbolService(self.manager, self.settings, self.audit).tick(symbol)
        freeze = float(info.get("trade_freeze_level", 0)) * float(info["point"])
        reference = float(tick["bid"])
        for level in (stop_loss, take_profit):
            if level is not None and abs(reference - level) < freeze:
                raise ValueError("SL/TP violates symbol freeze level.")
        raw = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_SLTP, "position": ticket,
            "symbol": symbol, "sl": stop_loss or 0.0, "tp": take_profit or 0.0})
        result = self._normalise(raw, MarketOrder(symbol, OrderSide.BUY, 0), reference, 0)
        self.audit.write("mt5.position.modify", self.settings.app_env.value,
            {"position": ticket, "result": result.audit_payload()}, str(context.session_id))
        return result

    def cancel_pending(self, context: TradingContext, ticket: int) -> ExecutionResult:
        self._ensure_live(context)
        raw = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": ticket})
        result = self._normalise(raw, MarketOrder("PENDING", OrderSide.BUY, 0), 0.0, 0)
        self.audit.write("mt5.order.cancel", self.settings.app_env.value,
            {"order": ticket, "result": result.audit_payload()}, str(context.session_id))
        return result
