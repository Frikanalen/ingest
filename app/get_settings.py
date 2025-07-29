from functools import lru_cache

from app.util.settings import Settings


@lru_cache
def get_settings() -> Settings:
    """Get the application settings, loading from environment variables."""
    return Settings()
