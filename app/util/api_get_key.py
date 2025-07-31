import logging

from frikanalen_django_api_client import Client
from frikanalen_django_api_client.api.obtain_token import obtain_token_create
from frikanalen_django_api_client.models import AuthTokenRequest

logger = logging.getLogger(__name__)


def api_get_key(api_url: str, username: str, password: str) -> str:
    """
    Helper function to obtain an API key using the provided username and password.
    This is used to authenticate the client with the Django API service.
    """
    logger.info("Obtaining API key for user: %s", username)
    login_client = Client(raise_on_unexpected_status=True, follow_redirects=True, base_url=str(api_url))
    response = obtain_token_create.sync_detailed(body=(AuthTokenRequest(username, password)), client=login_client)
    return response.parsed.token
