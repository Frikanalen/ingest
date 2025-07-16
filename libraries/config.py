# Global argument object
import os
from dataclasses import dataclass

args = None


@dataclass(frozen=True)
class DjangoConfiguration:
    base_url: str
    token: str
    dry_run: bool


@dataclass(frozen=True)
class IngestConfiguration:
    django: DjangoConfiguration


FK_API = os.environ.get("FK_API", "https://frikanalen.no/api")
FK_TOKEN = os.environ.get("FK_TOKEN")

config = IngestConfiguration(
    django=DjangoConfiguration(
        base_url=FK_API,
        token=FK_TOKEN,
        dry_run=os.environ.get("DRY_RUN", "false").lower() == "true",
    )
)
DIR = "/tmp"
TO_DIR = "/tank/media/"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
