"""What this deployment can build, for whoever is deciding what to queue.

Queueing work is an operator's act, run from a terminal against django-api, and
it needs one thing this repository holds and the catalogue does not: which
revision of each format the deployed image currently produces. `DESIRED_FORMATS`
and the revision each template carries are declared in `app.formats`, and a
second copy of them in the tool that queues work would be the half that rots --
bumping a revision here would silently need a matching edit there.

So it is published rather than copied, and read live rather than stored: if this
pod is not answering, the caller gets a connection error instead of a plausible
answer from whenever it last spoke.

Deliberately its own router rather than a path under `/internal`. This is the
only thing ingest serves that is reachable from outside the cluster, and keeping
it away from the hooks is what lets the ingress rule name one exact path -- see
`chart/templates/ingress.yaml`. It reads no request, touches no archive, takes
no arguments and answers a value computed at startup.

Unauthenticated, because reading it grants nothing. Queueing still goes through
django-api under the operator's own token, so the whole exposure is that a
stranger may learn which revision of DASH we are on.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.converge.chores import DesiredState


class DeployedFormats(BaseModel):
    """The answer to "what would a converged video have, here, now?"."""

    #: The image this pod is running, as the chart named it. Reported because
    #: the upload pod and the worker pool are separate Deployments off one
    #: image: mid-rollout this can answer with a revision that part of the pool
    #: cannot yet build, and a job queued at that revision is claimed, re-planned
    #: against the older template, rebuilt at the older revision and left stale
    #: with nothing to queue it again. Empty where the deployment did not say.
    image: str = Field(default="", description="Image this pod is running, if the deployment said")
    #: Variant name to the revision of the template currently producing it.
    formats: dict[str, int] = Field(description="Desired variants, and the revision each is currently at")


def create_router(image: str = "") -> APIRouter:
    """A router reporting `image` alongside what the shipped templates produce.

    The image is passed in rather than read from the settings at request time,
    so that this answers the same thing for the life of the process -- as does
    `DesiredState.from_templates()`, whose revisions come off files in the
    package and are cached.
    """
    router = APIRouter()

    @router.get("/formats", response_model=DeployedFormats)
    async def read_formats() -> DeployedFormats:
        desired = DesiredState.from_templates()
        return DeployedFormats(
            image=image,
            formats={str(variant): revision for variant, revision in desired.formats.items()},
        )

    return router
