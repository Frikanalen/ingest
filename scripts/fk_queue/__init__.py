"""The terminal end of the ingest queue.

Ingest converges a video toward what `app.formats` declares it should have.
Nothing in ingest decides *which* videos to converge: a worker converges the
one it was handed, and putting videos in front of workers is an operator's act,
run from a terminal against django-api.

So it lives here rather than in the application. What these tools share is a
shape: read the catalogue, ask the chores which videos have work in them, print
that, and with `--apply` put those videos in the queue. Nothing here does the
work itself, which is what makes closing the terminal harmless -- the queue is
the state, and the worker that claims a video decides again what it needs.

They read the catalogue and nothing else. That is not an economy but a
property: a plan's *actions* are a function of the videofile rows alone, and
only its notes -- media nothing claims, a row whose file is missing -- need the
archive in front of them. So an operator queueing work needs an API token and
no SSH key, and the worker, which has both, has the last word on every video.
"""
