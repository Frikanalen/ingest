from frikanalen_django_api_client import AuthenticatedClient

from app.config import config

api_client = AuthenticatedClient(config.django.base_url, config.django.token)
