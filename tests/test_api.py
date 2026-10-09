import os
import tempfile

os.environ["DATA_PROVIDER"] = "demo"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "journal.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_dashboard_and_analysis_flow():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        cfg = client.get("/api/config").json()
        assert cfg["provider"] == "demo"

        d = client.get("/api/analysis/NIFTY").json()
        assert d["symbol"] == "NIFTY"
        assert len(d["checks"]) == 6
        assert d["chart"]["candles"] and d["chart"]["m15"]["candles"]
        assert d["chain"]["pcr"] > 0
        assert {"P", "TC", "BC", "R1", "S1"} <= set(d["levels"])

        stock = client.get("/api/analysis/SBIN").json()
        assert stock["kind"] == "stock"
        assert client.get("/api/fundamentals/NIFTY").json()["available"] is False

        wl = client.post("/api/watchlist", json={"symbols": ["nifty", "sbin", "NIFTY"]}).json()
        assert wl["watchlist"] == ["NIFTY", "SBIN"]


def test_paper_trade_journal():
    with TestClient(app) as client:
        t = client.post("/api/trades", json={
            "symbol": "nifty", "direction": "CALL", "contract": "NIFTY 25000 CE",
            "entry_underlying": 25000, "stop_underlying": 24950, "target_underlying": 25075,
            "entry_premium": 100, "lots": 2, "lot_size": 65}).json()
        assert t["symbol"] == "NIFTY" and t["exit_time"] is None

        closed = client.post(f"/api/trades/{t['id']}/close", json={"exit_premium": 80, "reason": "stop"}).json()
        assert closed["pnl"] == (80 - 100) * 2 * 65

        stats = client.get("/api/trades").json()["stats"]
        assert stats["trades"] >= 1 and stats["losses"] >= 1

        assert client.post("/api/trades", json={"symbol": "X", "direction": "LONG"}).status_code == 422
        assert client.post("/api/trades/99999/close", json={}).status_code == 404
