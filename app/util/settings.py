import os
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DjangoApiSettingsPwdAuth(BaseModel):
    url: HttpUrl = Field()
    username: str = Field()
    password: SecretStr = Field()


class DjangoApiSettingsTokenAuth(BaseModel):
    url: HttpUrl = Field()
    token: SecretStr = Field()


class LocalArchiveSettings(BaseModel):
    """An archive on a filesystem ingest can write to directly."""

    # `extra="forbid"` is what keeps the archive union unambiguous: a config that
    # mentions a host cannot be mistaken for a local archive.
    model_config = ConfigDict(extra="forbid")

    dir: Path = Field(default=Path("./archive"), description="Directory where ingest should store archived files")


class SshArchiveSettings(BaseModel):
    """An archive on another host, read over SSH and mutated through it.

    The account named here has no write access to the archive. Reads go to a
    read-only SFTP server; every mutation is a request to `fk-archive`, which
    the storage host runs under sudo as the account that owns the media. See
    `app/archive_store/fk_archive.py`.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="Host serving the archive, e.g. file01")
    port: int = Field(default=22, description="SSH port on the archive host")
    username: str = Field(default="ingest", description="SSH user to log in as")
    dir: PurePosixPath = Field(
        default=PurePosixPath("/archive/media"),
        description=(
            "Directory on the archive host holding the archive. This is the path ingest reads from; "
            "writes are relative to the root the fk-archive profile on that host declares, so the two "
            "must name the same directory."
        ),
    )
    private_key_file: Path | None = Field(default=None, description="SSH private key to authenticate with")
    known_hosts_file: Path | None = Field(default=None, description="known_hosts file used to verify the archive host")
    connect_timeout: int = Field(default=30, description="Seconds to wait for the SSH connection to be established")

    fallback_dir: Path = Field(
        default=Path("./archive"),
        description="Local directory to archive into when the SSH credentials are missing, for local development",
    )
    required: bool = Field(
        default=False,
        description="Refuse to start rather than falling back to fallback_dir. Set this wherever losing files matters.",
    )

    def unusable_reason(self) -> str | None:
        """Why this archive cannot be used, or None if it can.

        Both credentials must be given explicitly: silently reaching for a
        developer's own ~/.ssh would make the archive depend on whoever happens
        to be running the process.
        """
        if self.private_key_file is None:
            return "no SSH private key configured"
        if not self.private_key_file.is_file():
            return f"SSH private key {self.private_key_file} does not exist"
        if self.known_hosts_file is None:
            return "no SSH known_hosts file configured"
        if not self.known_hosts_file.is_file():
            return f"SSH known_hosts file {self.known_hosts_file} does not exist"
        return None


class WorkerSettings(BaseModel):
    """A process that drains the ingest queue."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        default="",
        description="How this worker identifies itself when claiming. Empty uses the hostname, "
        "which in Kubernetes is the pod name and is what an operator will look for.",
    )
    kind: Literal["upload", "backfill"] | None = Field(
        default=None,
        description="Which queue this pool serves. Every job's source is the archive, so this is "
        "about who is waiting rather than what the worker can reach: unset serves both, which is "
        "the normal deployment, and `upload` runs a small lane that never queues behind a backfill.",
    )
    poll_interval_s: float = Field(
        default=30.0,
        description="How long to wait before asking again when the queue is empty",
    )

    def identify(self) -> str:
        return self.name or os.environ.get("HOSTNAME", "ingest-worker")


class IngestAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_nested_delimiter="_", env_nested_max_split=1, env_prefix="FK_"
    )

    api: DjangoApiSettingsPwdAuth | DjangoApiSettingsTokenAuth = Field(
        description="API configuration settings",
        default_factory=DjangoApiSettingsPwdAuth,
    )

    port: int = Field(default=8000, description="Port for the FastAPI server")
    host: str = Field(default="0.0.0.0", description="Host for the FastAPI server")

    # Observability only: nothing is decided on the strength of it. It is
    # reported by /ingest-api/formats so that an operator queueing work knows
    # which image answered -- the upload pod and the worker pool roll
    # separately, and a sweep run mid-rollout can plan against a revision half
    # the pool cannot build yet. Empty where the deployment did not say.
    image: str = Field(default="", description="Image this process is running, as the deployment named it")

    # Everything gated on this is a developer's convenience, not part of
    # serving tusd, and at least two of them are actively unwanted in a
    # deployment: FastAPI's debug mode returns tracebacks to the caller, and
    # the watch-folder observer recursively stats the whole upload volume once
    # a second. Off by default, so a deployment has to ask for them.
    debug: bool = Field(
        default=False,
        description="Enable the /watchFolder debug endpoints, the directory observer behind them, "
        "FastAPI's debug mode and DEBUG-level logging. Leave off in deployments.",
    )

    tusd_dir: Path = Field(
        default=Path("./upload"), description="Directory where ingest should look for uploads from tusd"
    )

    # tusd reports absolute paths as it sees them, which is only the same as
    # tusd_dir when both processes mount the upload volume alike. Keep this in
    # step with tusd's -upload-dir.
    tusd_upload_dir: PurePosixPath = Field(
        default=PurePosixPath("/upload"), description="Upload directory as tusd reports it, matching its -upload-dir"
    )

    # Setting FK_ARCHIVE_HOST is what selects the SSH archive; without it the
    # archive is a local directory and FK_ARCHIVE_DIR keeps its old meaning.
    archive: LocalArchiveSettings | SshArchiveSettings = Field(
        default_factory=LocalArchiveSettings, description="Where ingest should deposit finished files"
    )

    work_dir: Path | None = Field(
        default=None,
        description="Local scratch space for transcoding. Defaults to the system temporary directory.",
    )

    worker: WorkerSettings = Field(default_factory=WorkerSettings, description="Queue-draining behaviour")


@lru_cache
def get_settings() -> IngestAppSettings:
    """Get the application settings, loading from environment variables."""
    return IngestAppSettings()
