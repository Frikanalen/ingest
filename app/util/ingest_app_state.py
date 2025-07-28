from dataclasses import dataclass

from app.django_client.service import DjangoApiService


@dataclass
class IngestAppState:
    django_api: DjangoApiService
