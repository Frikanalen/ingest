"""Running the worker half of an ingest, in a test.

The hook queues a job and returns; a worker claims it and does the rest. A test
that wants to assert on the state the pair leaves behind has to do both, so
this is the second half.
"""

from app.worker import Worker


async def drain_one(archive, django_api, work_dir):
    """Claim and carry out jobs until the queue is empty, as the pool would."""
    worker = Worker(archive, django_api, name="test-worker", work_dir=work_dir, poll_interval_s=0)
    while await worker.run_once():
        pass
