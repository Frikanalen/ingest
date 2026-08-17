from functools import lru_cache
from pathlib import Path

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

    model_config = ConfigDict(extra="forbid")

    dir: Path = Field(default=Path("./archive"), description="Directory where ingest should store archived files")


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

    archive: LocalArchiveSettings = Field(
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
