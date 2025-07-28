import asyncio
import logging
import shlex

logger = logging.getLogger(__name__)


class Runner:
    def __init__(self):
        self.proc = None
        self.command = None

    async def run(self, command: str):
        self.command = command
        logger.info("Running: %s", command)

        self.proc = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        return self

    async def wait_for_completion(self):
        stdout, stderr = await self.proc.communicate()

        assert self.proc.returncode == 0, f"Command {self.command} failed, stderr: {stderr.decode()}"
