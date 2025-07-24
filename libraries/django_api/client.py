from frikanalen_django_api_client import AuthenticatedClient

from libraries.config import config

api_client = AuthenticatedClient(config.django.base_url, config.django.token)
