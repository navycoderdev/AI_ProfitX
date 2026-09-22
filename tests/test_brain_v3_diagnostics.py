from scripts.analyze_brain_v3_diagnostics import confidence_bucket, regime


def test_diagnostic_buckets_and_regimes_are_deterministic():
    assert confidence_bucket(.3999) == "[0.33,0.40)"
    assert confidence_bucket(.4) == "[0.40,0.50)"
    assert confidence_bucket(.6) == "[0.60,1.00]"
    assert regime({"m15_trend": "UP", "m30_trend": "UP", "h1_trend": "UP", "h4_trend": "UP",
                   "volatility_percentile": .7}) == "UP_HIGH_VOL"
    assert regime({"m15_trend": "UP", "m30_trend": "DOWN", "h1_trend": "UP", "h4_trend": "UP",
                   "volatility_percentile": .5}) == "MIXED_MID_VOL"
