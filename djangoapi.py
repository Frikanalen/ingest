import logging

import requests

from config import args


def _update_video(video_id, data):
    if args.no_api:
        logging.debug("NO API - updating video %s -> %r" % (video_id, data))
        return
    response = rq("PATCH", "/videos/%s" % video_id, data=data)


def get_videofiles(params):
    if args.no_api:
        logging.debug("NO API - get videofiles %s -> []" % params)
        return []
    response = rq("GET", "/videofiles/", params=params)
    return response.json()["results"]


def create_videofile(video_id, data):
    if args.no_api:
        logging.debug("NO API - creating videofile %s -> %r" % (video_id, data))
        return
    data.update({"video": video_id})
    rq("POST", "/videofiles/", data=data)


def update_videofile(data):
    videofile_id = data["id"]
    if args.no_api:
        logging.debug("NO API - updating videofile %s -> %r" % (videofile_id, data))
        return
    rq("PATCH", "/videofiles/%s" % videofile_id, data=data)


def rq(method, path, **kwargs):
    if args.no_api:
        raise Exception("Should not call request in no-api. Fix code.")
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=3)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    response = s.request(
        method,
        args.api + path,
        headers={"Authorization": "Token %s" % args.token},
        **kwargs,
    )
    response.raise_for_status()
    return response
