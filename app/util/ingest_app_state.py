from dataclasses import dataclass

from app.archive_store import ArchiveStore
from app.django_client.service import DjangoApiService


@dataclass
class IngestAppState:
    django_api: DjangoApiService
    archive: ArchiveStore
