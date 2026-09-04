"""What /ingest-api/formats answers, and what the ingress may reach.

This is the only thing ingest serves from outside the cluster, so the
interesting assertions are as much about what is *not* mounted beside it as
about the answer itself: the ingress rule names one exact path, and that path
must not be a way into the hooks.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.converge.chores import DesiredState
from app.main import create_app
from app.util.settings import DjangoApiSettingsPwdAuth, IngestAppSettings
from tests.get_git_root import get_git_root

API = DjangoApiSettingsPwdAuth(url="http://localhost:8000", username="ingest", password="hunter2")


def settings(**kwargs) -> IngestAppSettings:
    return IngestAppSettings(_env_file=None, api=API, **kwargs)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(settings(image="ghcr.io/frikanalen/ingest:v1.2.3")))


def test_it_reports_the_shipped_revisions(client):
    body = client.get("/ingest-api/formats").json()

    expected = {str(variant): revision for variant, revision in DesiredState.from_templates().formats.items()}
    assert body["formats"] == expected


def test_every_desired_format_is_named(client):
    """The answer is the desired set, not a subset of it a caller must complete."""
    body = client.get("/ingest-api/formats").json()

    assert set(body["formats"]) == {str(variant) for variant in DesiredState.from_templates().formats}
    assert all(revision >= 1 for revision in body["formats"].values())


def test_it_reports_the_image_it_was_told_about(client):
    assert client.get("/ingest-api/formats").json()["image"] == "ghcr.io/frikanalen/ingest:v1.2.3"


def test_an_unconfigured_deployment_reports_no_image():
    """Empty, not absent: a caller reading the field never has to case on its presence."""
    # Not entered as a context manager, here or in the fixture: that runs the
    # lifespan, which reaches for django-api and an archive. Nothing this
    # endpoint answers with comes from either.
    assert TestClient(create_app(settings())).get("/ingest-api/formats").json()["image"] == ""


def test_it_is_mounted_without_debug():
    """Unlike /watchFolder, this is part of serving the deployment."""
    paths = {route.path for route in create_app(settings()).routes}

    assert "/ingest-api/formats" in paths


def test_nothing_else_lives_under_the_exposed_prefix():
    """The ingress rule is Exact, but a second route here would still be a
    route someone would eventually be tempted to expose alongside it."""
    paths = {route.path for route in create_app(settings(debug=True)).routes}

    assert {path for path in paths if path.startswith("/ingest-api")} == {"/ingest-api/formats"}


def _ingress_paths() -> list[tuple[str, str]]:
    """Every (path, pathType) the ingress template declares.

    Read as text, with Helm expressions left as the opaque tokens they are.
    Not `helm template`: the question is which paths and match types the chart
    declares at all, and shelling out to Helm would make that answer depend on
    having it installed. Not YAML either -- stripping the actions to make it
    parse would drop the very lines a value comes from.
    """
    source = (get_git_root() / "chart" / "templates" / "ingress.yaml").read_text()
    return re.findall(r"- path:\s*(\S.*?)\s*\n\s*pathType:\s*(\w+)", source)


def test_the_ingress_exposes_the_formats_path_exactly():
    assert ("/ingest-api/formats", "Exact") in _ingress_paths()


def test_the_ingress_reaches_no_other_ingest_path():
    """A Prefix rule reaching the application would put the tusd hooks on the
    public internet. tusd's own rule is a prefix, but its value is a Helm
    expression pointing at tusd's port rather than at the application, so the
    assertion is that no *literal* prefix opens ingest up."""
    for path, path_type in _ingress_paths():
        if path_type == "Prefix":
            assert not path.startswith("/ingest-api")
            assert path != "/"
