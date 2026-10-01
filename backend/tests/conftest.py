import pytest


@pytest.fixture(autouse=True)
def _no_real_enrichment_enqueue(monkeypatch):
    calls = []
    import app.enrichment.hooks as hooks
    monkeypatch.setattr(hooks, "_delay", lambda *a, **k: calls.append((a, k)))
    yield calls
