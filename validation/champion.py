from validation.types import ValidationResult


class ChampionChallengerEngine:
    """Compares risk-adjusted evidence; challenger cannot win merely on total profit."""
    def compare(self, champion: dict, challenger: dict) -> ValidationResult:
        improvements = {"out_of_sample_net_pnl": challenger.get("out_of_sample_net_pnl", 0) - champion.get("out_of_sample_net_pnl", 0),
                        "drawdown_change": champion.get("maximum_drawdown", 0) - challenger.get("maximum_drawdown", 0),
                        "walk_forward_change": challenger.get("walk_forward_pass_rate", 0) - champion.get("walk_forward_pass_rate", 0),
                        "cost_stress_change": challenger.get("cost_stress_net_pnl", 0) - champion.get("cost_stress_net_pnl", 0)}
        passed = improvements["out_of_sample_net_pnl"] > 0 and improvements["drawdown_change"] >= 0 and improvements["walk_forward_change"] >= 0 and improvements["cost_stress_change"] >= 0
        return ValidationResult("champion_challenger", passed, improvements, () if passed else ("CHALLENGER_NOT_BETTER_ACROSS_RISK_MEASURES",))
