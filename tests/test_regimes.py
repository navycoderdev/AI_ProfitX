from datetime import datetime, timezone

from config.settings import Settings
from core.context import TradingContext
from core.modes import RuntimeEnvironment, TradingMode
from database.session import initialize_database
from memory.builder import ExperienceBuilder
from memory.repositories import DecisionMemoryRepository, MarketSnapshotRepository, TradeMemoryRepository
from memory.types import AIDecisionMemory, PreTradeSnapshot
from regimes.detector import DeterministicRegimeDetector
from regimes.reports import HistoricalRegimeReport
from regimes.repository import RegimeRepository
from regimes.research import RegimeResearchPipeline
from regimes.stability import RegimeStabilityManager
from regimes.types import RegimeLabel


def features(**overrides):
    result = {"feature_version": "phase3-v1", "volatility_percentile": .5, "trend_structure": "UP", "adx_proxy_14": 40,
              "break_structure_up": 0, "break_structure_down": 0, "spread_percentile": .4, "session": "LONDON"}
    result.update(overrides); return result


def test_deterministic_detector_has_confidence_and_supports_uncertain():
    now = datetime.now(timezone.utc); detector = DeterministicRegimeDetector()
    trend = detector.detect(features(), now)
    uncertain = detector.detect(features(adx_proxy_14=None), now)
    assert trend.label is RegimeLabel.TRENDING_UP and trend.confidence >= .6
    assert uncertain.label is RegimeLabel.UNCERTAIN
    assert trend.measurements["trend_structure"] == "UP"


def test_stability_filter_prevents_noise_label_switching_and_tracks_transition():
    detector, stable, now = DeterministicRegimeDetector(), RegimeStabilityManager(confirmations=2), datetime.now(timezone.utc)
    up = stable.apply(detector.detect(features(), now))
    noisy = stable.apply(detector.detect(features(trend_structure="DOWN"), now))
    switched = stable.apply(detector.detect(features(trend_structure="DOWN"), now))
    assert up.label is RegimeLabel.TRENDING_UP
    assert noisy.label is RegimeLabel.UNCERTAIN
    assert switched.label is RegimeLabel.TRENDING_DOWN and switched.transition is True


def test_research_pipeline_is_deterministic_and_explicitly_research_only():
    snapshots = [features(adx_proxy_14=20 + index, volatility_percentile=.1 * (index % 5), spread_percentile=.1) for index in range(8)]
    first, second = RegimeResearchPipeline(clusters=2), RegimeResearchPipeline(clusters=2)
    assert first.fit(snapshots)["centroids"] == second.fit(snapshots)["centroids"]
    cluster, confidence = first.predict_cluster(snapshots[0])
    assert cluster in (0, 1) and 0 < confidence <= 1


def test_regime_history_and_strategy_report_are_persisted(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/regimes.db"); sessions = initialize_database(settings); now = datetime.now(timezone.utc)
    detector = DeterministicRegimeDetector(); assessment = detector.detect(features(), now)
    regime_id = RegimeRepository(sessions).record("EURUSD", "M5", assessment, "decision-1")
    assert RegimeRepository(sessions).history("EURUSD", "M5")[0]["regime_id"] == regime_id
    builder = ExperienceBuilder(MarketSnapshotRepository(sessions), DecisionMemoryRepository(sessions), TradeMemoryRepository(sessions))
    context = TradingContext(None, "EURUSD", "M5", "TRENDING_UP", "model", "strategy", "risk", RuntimeEnvironment.TEST, TradingMode.PAPER)
    decision = builder.record_decision(context, PreTradeSnapshot(now, "EURUSD", "M5", {}, regime="TRENDING_UP"), AIDecisionMemory("NO_TRADE", .5, None, None, None), None)
    report = HistoricalRegimeReport(sessions).strategy_performance("strategy")
    assert report["TRENDING_UP"]["decisions"] == 1
    assert report["TRENDING_UP"]["no_trade_decisions"] == 1
