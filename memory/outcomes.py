from memory.types import OutcomeLabel, PostTradeOutcome


class OutcomeLabeler:
    """Labels observed outcome, not counterfactual decision quality; trading outcomes remain uncertain."""
    def label(self, outcome: PostTradeOutcome) -> OutcomeLabel:
        r_multiple = outcome.pnl_after_costs / outcome.risk_amount if outcome.pnl_after_costs is not None and outcome.risk_amount not in (None, 0) else None
        flags = []
        if outcome.market_context.get("regime_changed"): flags.append("REGIME_CHANGED_AFTER_ENTRY")
        if outcome.market_context.get("spread_widened"): flags.append("SPREAD_WIDENED")
        if outcome.market_context.get("news_window"): flags.append("NEWS_WINDOW")
        if outcome.pnl_after_costs is None: return OutcomeLabel("UNRESOLVED", 0.0, "Trade outcome is not complete.", r_multiple, context_flags=tuple(flags))
        if abs(outcome.pnl_after_costs) < (outcome.risk_amount or 1) * .05:
            return OutcomeLabel("NEUTRAL_CONTEXT_DEPENDENT", .4, "Outcome is near flat; decision quality remains undetermined.", r_multiple, context_flags=tuple(flags))
        direction = "FAVORABLE" if outcome.pnl_after_costs > 0 else "ADVERSE"
        return OutcomeLabel(f"{direction}_CONTEXT_DEPENDENT", .5,
            "Observed P&L is one outcome and is not a verdict on decision quality.", r_multiple, context_flags=tuple(flags))
