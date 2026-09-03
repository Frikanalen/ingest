"""Where an operator's API token comes from.

`~/.frikanalen.yaml` is what fk-cli logs in with, so an operator who has just
logged in does not then have to find the token and pass it in by hand. Read
rather than reimplemented, and read the same way `fk-archive-gc` reads it.

Not ingest's own settings: those are FK_* environment variables describing a
deployment, and a person queueing work from a laptop has no deployment. The two
answer different questions -- "how does this pod reach django-api" and "who is
running this command" -- and a tool that took the first would authenticate as
ingest itself against whatever the working directory's `.env` last said.
"""

import os
from dataclasses import dataclass
from pathlib import Path

#: Where fk-cli keeps the session it logged in with.
DEFAULT_CONFIG = "~/.frikanalen.yaml"

#: Where each environment's django-api is, for a configuration file that names
#: only a token. fk-cli writes an `api` key for an environment it was pointed
#: at explicitly and leaves it out for the ones it knows, so without these the
#: usual file would need `--api-url` on every invocation.
DEFAULT_API_URLS = {
    "local": "http://localhost:8000",
    "staging": "https://staging.frikanalen.no",
    "prod": "https://frikanalen.no",
}


class CredentialsError(RuntimeError):
    """The configuration file cannot answer who we are talking to, or as whom."""


@dataclass(frozen=True)
class Credentials:
    api_url: str
    token: str
    environment: str


def load(
    *,
    environment: str | None = None,
    config_path: str | None = None,
    api_url: str | None = None,
) -> Credentials:
    """Read the API token fk-cli logged in with, for one named environment.

    The environment defaults to the file's own `environment:` key -- the one
    fk-cli currently has selected -- because everything these tools touch is in
    that one catalogue. `fk-archive-gc` deliberately does not default that way,
    but it is pairing a catalogue with an archive and can pair the wrong two;
    here there is only ever the one.
    """
    path = Path(os.path.expanduser(config_path or os.environ.get("FRIKANALEN_CONFIG") or DEFAULT_CONFIG))
    config = _read(path)

    environment = environment or config.get("environment")
    if not environment:
        raise CredentialsError(f"{path} selects no environment. Pass --environment, or log in with fk-cli.")

    settings = (config.get("environments") or {}).get(environment) or {}
    if not isinstance(settings, dict):
        raise CredentialsError(f"{path} has no usable settings for the {environment} environment")

    token = settings.get("token")
    if not token:
        # The message a tired operator actually needs, rather than a KeyError.
        # A machine that has only ever talked to staging has no prod stanza at
        # all, and that is the usual reason to be here.
        raise CredentialsError(
            f"no API token for the {environment} environment in {path}. Log in to {environment} with fk-cli first."
        )

    url = api_url or settings.get("api") or DEFAULT_API_URLS.get(environment)
    if not url:
        raise CredentialsError(
            f"the {environment} environment in {path} has no `api` URL, and there is no default for a "
            f"name outside {sorted(DEFAULT_API_URLS)}. Add one, or pass --api-url."
        )

    return Credentials(api_url=str(url).rstrip("/"), token=str(token), environment=environment)


def _read(path: Path) -> dict:
    import yaml

    try:
        config = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as e:
        raise CredentialsError(f"{path} does not exist. Log in with fk-cli first.") from e
    except yaml.YAMLError as e:
        raise CredentialsError(f"{path} is not valid YAML: {e}") from e

    if not isinstance(config, dict):
        raise CredentialsError(f"{path} does not hold a mapping")
    return config
