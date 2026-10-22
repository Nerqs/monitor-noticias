import pytest

from monitor import db, preferences


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Cada teste usa uma pasta de dados vazia."""
    monkeypatch.setenv("MONITOR_DATA_DIR", str(tmp_path))
    preferences._model = None
    yield tmp_path
    db.close()
