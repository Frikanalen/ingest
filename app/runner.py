import asyncio
import logging
from asyncio import subprocess
from collections.abc import Awaitable, Callable

from app.media.ffmpeg_progress import FfmpegProgressParser

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], Awaitable[None]]


class Task:
    """A task that can be executed asynchronously.
    Raises ChildProcessError if the command fails.
    """

    command_line: str

    def __init__(
        self,
        command_line: str,
        duration_s: float | None = None,
        passes: int = 1,
        on_progress: ProgressCallback | None = None,
    ):
        """
        `duration_s` and `on_progress` are how far ffmpeg has got, not
        whether it succeeded -- that's still the return value/exception
        from `execute()`. Without them we just drain stdout and report
        nothing in between.
        """
        self.command_line = command_line
        self.duration_s = duration_s
        self.passes = passes
        self.on_progress = on_progress
        self.proc = subprocess.create_subprocess_shell(command_line, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.debug("Created task for command: %s", command_line)

    async def execute(self) -> tuple[str, str]:
        proc = await self.proc

        # Both pipes must be drained concurrently, or a chatty one fills its
        # OS buffer and blocks the child while we're still waiting on the
        # other.
        stderr_task = asyncio.create_task(proc.stderr.read())
        stdout = await self._read_stdout(proc)
        stderr = await stderr_task
        await proc.wait()

        if proc.returncode != 0:
            raise ChildProcessError(f"Command failed - stdout: {stdout.decode()}, stderr: {stderr.decode()}")

        return stdout.decode(), stderr.decode()

    async def _read_stdout(self, proc: subprocess.Process) -> bytes:
        if not (self.on_progress and self.duration_s):
            return await proc.stdout.read()

        parser = FfmpegProgressParser(duration_s=self.duration_s, passes=self.passes)
        chunks: list[bytes] = []
        async for line in proc.stdout:
            chunks.append(line)
            fraction = parser.feed_line(line.decode(errors="replace"))
            if fraction is not None:
                await self.on_progress(fraction)
        return b"".join(chunks)
