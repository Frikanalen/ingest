from frikanalen_django_api_client import AuthenticatedClient

from app.util.settings import settings

api_client = AuthenticatedClient(str(settings.api.url), settings.api.key.get_secret_value())
