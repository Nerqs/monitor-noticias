import pytest

from monitor import cveinfo, db, fetchers, preferences, scheduler, selfcheck


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Cada teste usa uma pasta de dados vazia e nada sai para a internet."""
    monkeypatch.setenv("MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fetchers, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(selfcheck, "run_if_due", lambda cfg: None)
    monkeypatch.setattr(cveinfo, "fetch_epss", lambda client, cves: {})
    monkeypatch.setattr(scheduler, "is_installed", lambda: False)
    monkeypatch.setattr(scheduler, "resident_installed", lambda: False)
    preferences._model = None
    yield tmp_path
    db.close()
