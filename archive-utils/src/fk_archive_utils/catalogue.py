"""Just enough django-api to finish the `broadcast/` migration.

Temporary, and deliberately shaped so it is obvious that it is: this package
otherwise knows nothing about the catalogue, and has no business knowing.
It exists because moving a video's source from `broadcast/` to `original/` is
half an archive operation and half a database one, and doing only the archive
half would leave rows naming files that are no longer there.

When the last `broadcast/` directory is gone, this module, `migrate_broadcast`,
their entry point and the python3-yaml dependency all go together.

urllib rather than requests or httpx, and PyYAML only in the one function that
needs it: the commands an SSH session can reach import nothing outside the
standard library, and that stays true.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fk_archive_utils.errors import CatalogueError

#: Where fk-cli keeps the session it logged in with. Read rather than
#: reimplemented: an operator who has just run fk-cli should not then have to
#: find the token and pass it in by hand.
DEFAULT_CONFIG = "~/.frikanalen.yaml"

#: Long enough for a catalogue under load, short enough that a migration over
#: thousands of videos does not silently hang on one of them.
TIMEOUT_S = 30


@dataclass(frozen=True)
class Credentials:
    api_url: str
    token: str
    environment: str


def load_credentials(
    environment: str,
    *,
    config_path: str | None = None,
    api_url: str | None = None,
) -> Credentials:
    """Read the API token fk-cli logged in with, for one named environment.

    Read before privileges are dropped, because the file belongs to whoever is
    running this and is very unlikely to be readable by the archive account.

    The environment is passed in -- it defaults to the archive profile's own
    name -- rather than taken from the file's `environment:` key, which is
    whatever fk-cli was last pointed at. Migrating the production archive
    against whichever catalogue an operator happened to have selected is the
    one mistake here that cannot be undone with a rename, so the two are tied
    together by default and only `--environment` can separate them.
    """
    path = Path(os.path.expanduser(config_path or os.environ.get("FRIKANALEN_CONFIG") or DEFAULT_CONFIG))

    try:
        import yaml
    except ImportError as e:  # pragma: no cover - the package depends on it
        raise CatalogueError("python3-yaml is not installed, so ~/.frikanalen.yaml cannot be read") from e

    try:
        config = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as e:
        raise CatalogueError(f"{path} does not exist. Log in with fk-cli first.") from e
    except yaml.YAMLError as e:
        raise CatalogueError(f"{path} is not valid YAML: {e}") from e

    if not isinstance(config, dict):
        raise CatalogueError(f"{path} does not hold a mapping")

    environments = config.get("environments") or {}
    settings = environments.get(environment) or {}
    if not isinstance(settings, dict):
        raise CatalogueError(f"{path} has no usable settings for the {environment} environment")

    token = settings.get("token")
    if not token:
        # The message a tired operator actually needs, rather than a KeyError.
        # A host that has only ever talked to staging has no prod stanza at
        # all, and that is the usual reason to be here.
        raise CatalogueError(
            f"no API token for the {environment} environment in {path}. "
            f"Log in to {environment} with fk-cli first."
        )

    url = api_url or settings.get("api")
    if not url:
        raise CatalogueError(
            f"the {environment} environment in {path} has no `api` URL. Add one, or pass --api-url."
        )

    return Credentials(api_url=str(url).rstrip("/"), token=str(token), environment=environment)


class Catalogue:
    """The three django-api calls the migration makes, and nothing else."""

    def __init__(self, credentials: Credentials, *, dry_run: bool = False):
        self.credentials = credentials
        self.dry_run = dry_run

    def video_exists(self, video_id: str) -> bool:
        """Whether the catalogue still has a row for this video.

        A video it has dropped is not this migration's to touch: the backfill's
        garbage collection takes the whole directory, and moving files around
        inside one first would only make that harder to read.
        """
        try:
            self._request("GET", f"/api/videos/{video_id}")
        except CatalogueError as e:
            if e.status == 404:
                return False
            raise
        return True

    def files_for_video(self, video_id: str) -> list[dict]:
        """Every videofile row for this video, following pagination."""
        rows: list[dict] = []
        path = f"/api/videofiles?{urllib.parse.urlencode({'video_id': video_id, 'limit': 100})}"
        while path:
            page = self._request("GET", path)
            rows.extend(page.get("results") or [])
            following = page.get("next")
            # The API returns an absolute URL; keep only the part after the
            # host so a redirect cannot walk this off to somewhere else.
            path = urllib.parse.urlsplit(following).path if following else ""
            if following:
                query = urllib.parse.urlsplit(following).query
                path = f"{path}?{query}" if query else path
        return rows

    def retag(self, file_id: int, *, variant: str, filename: str) -> None:
        """Point an existing row at where its file now is.

        Updating the record rather than replacing it, so the file keeps
        whatever history and identity the row already carried.
        """
        if self.dry_run:
            return
        self._request("PATCH", f"/api/videofiles/{file_id}", body={"variant": variant, "filename": filename})

    def unregister(self, file_id: int) -> None:
        """Drop a row whose file has been trashed.

        Only ever after the trash, never before: reversed, a failure between
        the two would destroy the record of media it then failed to remove.
        """
        if self.dry_run:
            return
        self._request("DELETE", f"/api/videofiles/{file_id}")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.credentials.api_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Token {self.credentials.token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                payload = response.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            failure = CatalogueError(f"{method} {path} failed: {e.code} {e.reason} {detail}".strip())
            failure.status = e.code
            raise failure from e
        except urllib.error.URLError as e:
            raise CatalogueError(f"{method} {path} could not reach {self.credentials.api_url}: {e.reason}") from e

        return json.loads(payload) if payload else {}
