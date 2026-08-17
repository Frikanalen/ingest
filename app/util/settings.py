from functools import lru_cache
from pathlib import Path, PurePosixPath

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
    """An archive on another host, written over SSH."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="Host serving the archive, e.g. file01")
    port: int = Field(default=22, description="SSH port on the archive host")
    username: str = Field(default="ingest", description="SSH user to log in as")
    dir: PurePosixPath = Field(
        default=PurePosixPath("/archive/media"), description="Directory on the archive host to store archived files in"
    )
    private_key_file: Path | None = Field(default=None, description="SSH private key to authenticate with")
    known_hosts_file: Path | None = Field(default=None, description="known_hosts file used to verify the archive host")
    connect_timeout: int = Field(default=30, description="Seconds to wait for the SSH connection to be established")

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

    tusd_dir: Path = Field(
        default=Path("./upload"), description="Directory where ingest should look for uploads from tusd"
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


@lru_cache
def get_settings() -> IngestAppSettings:
    """Get the application settings, loading from environment variables."""
    return IngestAppSettings()
