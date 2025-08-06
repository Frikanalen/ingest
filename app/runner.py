import logging
from asyncio import subprocess

logger = logging.getLogger(__name__)


class Task:
    """A task that can be executed asynchronously.
    Raises ChildProcessError if the command fails.
    """

    command_line: str

    def __init__(self, command_line: str):
        self.command_line = command_line
        self.proc = subprocess.create_subprocess_shell(command_line, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.debug("Created task for command: %s", command_line)

    async def execute(self) -> tuple[str, str]:
        proc = await self.proc
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise ChildProcessError(f"Command failed - stdout: {stdout.decode()}, stderr: {stderr.decode()}")

        return stdout.decode(), stderr.decode()
