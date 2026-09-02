"""What `FK_DEBUG` turns on, and that nothing turns it on by accident.

The /watchFolder endpoints and the directory observer behind them are a
developer's view of the upload spool. The observer is a `PollingObserver`: it
walks the tree and stats every entry once a second, and in production that tree
is the 200 GiB volume shared with tusd. So the interesting assertion is the
negative one -- with the default settings, no route is mounted and no observer
is ever scheduled.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.main import create_app
from app.util.settings import DjangoApiSettingsPwdAuth, IngestAppSettings

API = DjangoApiSettingsPwdAuth(url="http://localhost:8000", username="ingest", password="hunter2")


@pytest.fixture(autouse=True)
def clean_debug_env(monkeypatch):
    monkeypatch.delenv("FK_DEBUG", raising=False)


def settings(**kwargs) -> IngestAppSettings:
    return IngestAppSettings(_env_file=None, api=API, **kwargs)


def paths(app) -> set[str]:
    return {route.path for route in app.routes}


def test_debug_is_off_by_default():
    assert settings().debug is False


def test_fk_debug_turns_it_on(monkeypatch):
    monkeypatch.setenv("FK_DEBUG", "true")

    assert settings().debug is True


def test_watch_folder_is_not_mounted_by_default():
    app = create_app(settings())

    assert not [path for path in paths(app) if path.startswith("/watchFolder")]


def test_watch_folder_is_mounted_in_debug():
    app = create_app(settings(debug=True))

    assert "/watchFolder/tusFiles" in paths(app)
    assert "/watchFolder/archive" in paths(app)


def test_the_hooks_are_mounted_either_way():
    for debug in (False, True):
        assert "/tusdHooks/" in paths(create_app(settings(debug=debug)))
        assert "/internal/isAlive" in paths(create_app(settings(debug=debug)))


def test_an_unconfigured_environment_gets_no_debug_features():
    """`app.main` is imported before anything validates the configuration."""
    app = create_app(None)

    assert app.debug is False
    assert not [path for path in paths(app) if path.startswith("/watchFolder")]


def test_fastapi_debug_mode_follows_the_setting():
    """FastAPI's own debug mode returns tracebacks to the caller."""
    assert create_app(settings()).debug is False
    assert create_app(settings(debug=True)).debug is True


class _StubClient:
    """Stands in for AuthenticatedClient, which lifespan enters as a context."""

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@asynccontextmanager
async def _run_lifespan(app_settings: IngestAppSettings):
    """Run the real lifespan with only the outside world stubbed out."""
    from app.util import lifespan as lifespan_module

    with (
        patch.object(lifespan_module, "get_settings", return_value=app_settings),
        patch.object(lifespan_module, "get_token", return_value="token"),
        patch.object(lifespan_module, "AuthenticatedClient", _StubClient),
        patch.object(lifespan_module, "create_archive_store", return_value=object()),
    ):
        app = create_app(app_settings)
        async with lifespan_module.lifespan(app):
            yield


@pytest.mark.asyncio
async def test_no_observer_is_started_by_default():
    from app.api.debug.watch_folder import watcher

    with (
        patch.object(watcher, "start_watchfolder") as start,
        patch.object(watcher, "stop_watch_folder") as stop,
    ):
        async with _run_lifespan(settings()):
            pass

    start.assert_not_called()
    stop.assert_not_called()


@pytest.mark.asyncio
async def test_the_observer_starts_and_stops_in_debug(tmp_path):
    from app.api.debug.watch_folder import watcher

    app_settings = settings(debug=True, tusd_dir=tmp_path)

    with (
        patch.object(watcher, "start_watchfolder") as start,
        patch.object(watcher, "stop_watch_folder") as stop,
    ):
        async with _run_lifespan(app_settings):
            start.assert_called_once_with(tmp_path)
            stop.assert_not_called()

        stop.assert_called_once_with()
