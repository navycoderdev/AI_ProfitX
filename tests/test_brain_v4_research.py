from research.brain_v4 import enriched_features, head_key, normalized_spread_gate, stop_target


CONTRACT = {"point": .01, "tick_size": .01, "digits": 2, "contract_size": 1., "volume_min": .01,
            "currency_profit": "USD", "spread_points_snapshot": 500}


def test_stop_target_is_atr_and_contract_driven():
    proposal = stop_target("BTCUSD", "LONG", 80000., 100., CONTRACT)
    assert proposal["stop_loss"] == 79800.
    assert proposal["take_profit"] == 80300.
    assert proposal["risk_reward"] == 1.5


def test_normalized_spread_gate_is_dimensionless_and_auditable():
    assert normalized_spread_gate("BTCUSD", 500, 100, 80000, 10000, CONTRACT)["approved"]
    assert not normalized_spread_gate("BTCUSD", 1500, 100, 80000, 10000, CONTRACT)["approved"]


def test_heads_and_enriched_features_are_deterministic():
    assert head_key("EURUSD", "asset_family_heads") == "FX"
    row = {"symbol": "BTCUSD", "features": {"rolling_high_20": 101., "rolling_low_20": 99.,
        "atr_14": 2., "candle_body": 1., "hour_utc": 6., "m15_trend": 1., "m30_trend": 1.,
        "h1_trend": -1., "h4_trend": 1., "volatility_percentile": .5}}
    values = enriched_features(row, {"BTCUSD": CONTRACT})
    assert values["atr_price_fraction"] == .02
    assert values["trend_alignment"] == .5
