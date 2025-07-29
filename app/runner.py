import logging
from asyncio.subprocess import Process
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class Task:
    """A task that can be executed asynchronously.
    Raises ChildProcessError if the command fails.
    """

    proc: Coroutine[Any, Any, Process]

    def __init__(self, proc: Coroutine[Any, Any, Process]):
        self.proc = proc

    async def execute(self) -> str:
        proc = await self.proc
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise ChildProcessError(f"Command failed - stdout: {stdout.decode()}, stderr: {stderr.decode()}")

        return stdout.decode(), stderr.decode()
