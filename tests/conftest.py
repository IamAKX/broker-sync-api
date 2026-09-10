import pytest

from app.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_response_cache():
    """The in-process TTL cache (app/core/cache.py) is a module global — reset
    it around every test so one test's cached payload can't leak into the next."""
    cache.clear()
    yield
    cache.clear()
