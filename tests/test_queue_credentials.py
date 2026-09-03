"""Reading an operator's API token out of ~/.frikanalen.yaml.

Every failure here is one a tired operator hits at the worst moment -- a
machine that has only ever talked to staging, a file fk-cli has never written
-- so each says what to do about it rather than raising a KeyError.
"""

import pytest

from fk_queue.credentials import CredentialsError, load

TOKEN = "28947e0c1ec5d12489d524991a26b6ecac42a9a8"

#: An environment that names a URL of its own, which the defaults must not win over.
STAGING_WITH_URL = (
    "environment: staging\n"
    "environments:\n"
    "  staging:\n"
    "    api: http://elsewhere:8000\n"
    f"    token: {TOKEN}\n"
)


@pytest.fixture
def config(tmp_path):
    """A configuration file as fk-cli writes it, minus whatever a test drops."""

    def write(body: str) -> str:
        path = tmp_path / "frikanalen.yaml"
        path.write_text(body)
        return str(path)

    return write


def test_the_selected_environment_is_the_one_used(config):
    path = config(
        "environment: staging\n"
        "environments:\n"
        "  local:\n"
        "    api: http://localhost:8000\n"
        "    token: local-token\n"
        f"  staging:\n    token: {TOKEN}\n"
    )

    credentials = load(config_path=path)

    assert credentials.environment == "staging"
    assert credentials.token == TOKEN


def test_an_environment_naming_only_a_token_gets_its_own_url(config):
    """What the file usually looks like: fk-cli writes an `api` key only for an
    environment it was pointed at by hand."""
    path = config(f"environment: staging\nenvironments:\n  staging:\n    token: {TOKEN}\n")

    assert load(config_path=path).api_url == "https://staging.frikanalen.no"


def test_the_files_own_url_wins_over_the_default(config):
    assert load(config_path=config(STAGING_WITH_URL)).api_url == "http://elsewhere:8000"


def test_an_explicit_url_wins_over_the_file(config):
    credentials = load(config_path=config(STAGING_WITH_URL), api_url="http://mine:8000/")

    assert credentials.api_url == "http://mine:8000"


def test_an_environment_can_be_asked_for_explicitly(config):
    path = config(
        "environment: staging\n"
        "environments:\n"
        f"  staging:\n    token: {TOKEN}\n"
        "  prod:\n    token: prod-token\n"
    )

    credentials = load(config_path=path, environment="prod")

    assert credentials.token == "prod-token"
    assert credentials.api_url == "https://frikanalen.no"


def test_an_environment_with_no_token_says_to_log_in(config):
    """The usual reason to be here: this machine has never talked to prod."""
    path = config(f"environment: prod\nenvironments:\n  staging:\n    token: {TOKEN}\n")

    with pytest.raises(CredentialsError, match="Log in to prod"):
        load(config_path=path)


def test_an_environment_nobody_has_a_url_for_asks_for_one(config):
    path = config("environment: file01\nenvironments:\n  file01:\n    token: t\n")

    with pytest.raises(CredentialsError, match="--api-url"):
        load(config_path=path)


def test_a_file_that_selects_nothing_says_so(config):
    path = config(f"environments:\n  staging:\n    token: {TOKEN}\n")

    with pytest.raises(CredentialsError, match="--environment"):
        load(config_path=path)


def test_a_missing_file_says_to_log_in(tmp_path):
    with pytest.raises(CredentialsError, match="does not exist"):
        load(config_path=str(tmp_path / "nothing.yaml"))


def test_a_file_that_is_not_yaml_says_which_file(config):
    path = config("environment: [staging\n")

    with pytest.raises(CredentialsError, match="not valid YAML"):
        load(config_path=path)
