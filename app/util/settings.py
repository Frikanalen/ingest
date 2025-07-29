import os
from pathlib import Path

from pydantic import BaseModel, DirectoryPath, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiConfig(BaseModel):
    url: HttpUrl = Field()
    username: str = Field()
    password: SecretStr = Field()


class DebugConfig(BaseModel):
    watchdir: DirectoryPath = Path("./upload")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="_", env_nested_max_split=1, env_prefix="FK_")

    api: ApiConfig = Field(default_factory=ApiConfig, description="API configuration settings")
    debug: DebugConfig = Field(default_factory=DebugConfig, description="Debug configuration settings")

    port: int = Field(default=8000, description="Port for the FastAPI server")
    host: str = Field(default="0.0.0.0", description="Host for the FastAPI server")

    archive_dir: DirectoryPath = Path("./archive")


settings = Settings()  # Will raise ValidationError if any required vars are missing


DIR = "/tmp"
TO_DIR = "/tank/media/"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
