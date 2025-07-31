import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, DirectoryPath, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DjangoApiSettingsPwdAuth(BaseModel):
    url: HttpUrl = Field()
    username: str = Field()
    password: SecretStr = Field()


class DjangoApiSettingsTokenAuth(BaseModel):
    url: HttpUrl = Field()
    token: SecretStr = Field()


def get_discriminator_value(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("fruit", v.get("filling"))
    return getattr(v, "fruit", getattr(v, "filling", None))


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

    tusd_dir: DirectoryPath = Path("./upload", description="Directory where ingest should look for uploads from tusd")
    archive_dir: DirectoryPath = Path("./archive", description="Directory where ingest should store processed files")


DIR = "/tmp"
TO_DIR = "/tank/media/"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


@lru_cache
def get_settings() -> IngestAppSettings:
    """Get the application settings, loading from environment variables."""
    return IngestAppSettings()
