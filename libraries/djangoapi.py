import logging
import requests
from requests.adapters import HTTPAdapter


def _create_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=3)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class ApiClient:
    BASE_HEADERS = {"Authorization": "Token {token}"}
    NO_API_LOG_FORMAT = "NO API - {action} {entity} {id} -> {data}"

    def __init__(self, base_url, token, no_api=False):
        self.base_url = base_url
        self.token = token
        self.no_api = no_api

    def _log_no_api(self, action, entity, entity_id, data):
        logging.debug(
            self.NO_API_LOG_FORMAT.format(
                action=action, entity=entity, id=entity_id, data=data
            )
        )

    def make_request(self, method, path, **kwargs):
        if self.no_api:
            raise Exception("Should not call request in no-api. Fix code.")

        session = _create_session()
        headers = self.BASE_HEADERS.copy()
        headers["Authorization"] = headers["Authorization"].format(token=self.token)

        response = session.request(
            method, f"{self.base_url}{path}", headers=headers, **kwargs
        )
        response.raise_for_status()
        return response

    def update_video(self, video_id, data):
        if self.no_api:
            self._log_no_api("updating", "video", video_id, data)
            return
        self.make_request("PATCH", f"/videos/{video_id}", data=data)

    def get_videofiles(self, params):
        if self.no_api:
            self._log_no_api("get", "videofiles", params, "[]")
            return []
        response = self.make_request("GET", "/videofiles/", params=params)
        return response.json()["results"]

    def create_videofile(self, video_id, data):
        if self.no_api:
            self._log_no_api("creating", "videofile", video_id, data)
            return
        data.update({"video": video_id})
        self.make_request("POST", "/videofiles/", data=data)

    def update_videofile(self, data):
        videofile_id = data["id"]
        if self.no_api:
            self._log_no_api("updating", "videofile", videofile_id, data)
            return
        self.make_request("PATCH", f"/videofiles/{videofile_id}", data=data)


from .config import config


# Create global instance for backward compatibility
api_client = ApiClient(
    base_url=config.django.base_url,
    token=config.django.token,
    no_api=config.django.dry_run,
)


# Expose functions as module-level functions for backward compatibility
def update_video(video_id, data):
    return api_client.update_video(video_id, data)


def get_videofiles(params):
    return api_client.get_videofiles(params)


def create_videofile(video_id, data):
    return api_client.create_videofile(video_id, data)


def update_videofile(data):
    return api_client.update_videofile(data)
