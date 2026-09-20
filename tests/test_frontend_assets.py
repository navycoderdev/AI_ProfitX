from pathlib import Path

from app.main import control_center_frontend


ROOT = Path(__file__).resolve().parents[1]


def test_control_center_root_serves_frontend_document() -> None:
    response = control_center_frontend()
    assert Path(response.path).name == "index.html"
    assert Path(response.path).is_file()


def test_frontend_uses_control_center_endpoints_without_embedded_prices() -> None:
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    for endpoint in ("/control/overview", "/control/live-market", "/control/ai-brain", "/control/positions", "/control/trade-memory", "/control/model-lab", "/control/risk-center", "/control/system-health", "/control/audit-log"):
        assert endpoint in script
    assert "setInterval(refresh,3000)" in script
    assert "EURUSD" not in script


def test_frontend_has_responsive_layout_and_accessible_navigation() -> None:
    document = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
    assert 'aria-label="Primary navigation"' in document
    assert "@media(max-width:900px)" in styles
