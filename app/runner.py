import asyncio
import logging
from asyncio import subprocess
from collections.abc import Awaitable, Callable

from app.media.ffmpeg_progress import FfmpegProgressParser

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], Awaitable[None]]

#: How much of a stream to keep back when the output itself is not wanted.
#: The only reader is the failure message, and ffmpeg's last words are the
#: informative ones -- the same reasoning as `IngestReporter.failed`, which
#: trims what it is handed to the 1000 characters its column holds.
FAILURE_TAIL_BYTES = 4000

#: Read size for a stream we are only tailing. Big enough that a chatty child
#: does not cost a syscall per progress block.
_READ_CHUNK = 64 * 1024


class _Output:
    """What is kept of one of a child's output streams.

    Either all of it, or a bounded tail. Which one is `Task`'s decision --
    see the two ways of running one there.
    """

    def __init__(self, keep_everything: bool, limit: int = FAILURE_TAIL_BYTES):
        self._keep_everything = keep_everything
        self._limit = limit
        self._chunks: list[bytes] = []
        self._tail = b""

    def add(self, data: bytes) -> None:
        if self._keep_everything:
            self._chunks.append(data)
        else:
            self._tail = (self._tail + data)[-self._limit :]

    async def consume(self, stream: asyncio.StreamReader) -> None:
        """Read a stream to EOF. For a stream nobody is parsing as it arrives."""
        if self._keep_everything:
            self.add(await stream.read())
            return
        while chunk := await stream.read(_READ_CHUNK):
            self.add(chunk)

    def text(self) -> str:
        if self._keep_everything:
            return b"".join(self._chunks).decode()
        # A tail can begin in the middle of a character, so it is decoded
        # leniently; a stream kept in full is not, because a caller reading
        # output for its content should hear about output that is not text.
        return self._tail.decode(errors="replace")


class Task:
    """A task that can be executed asynchronously.
    Raises ChildProcessError if the command fails.

    There are two ways to run one, and which you want depends on what the
    command's output is for.

    `execute()` is for a command whose output is the answer -- ffprobe's JSON,
    loudnorm's report -- and hands back all of it.

    `run()` is for a command whose output is progress. ffmpeg with
    `-progress pipe:1` writes a block every half second for as long as the
    encode takes, and nobody reads a line of it; holding on to all of that
    would make a transcode cost memory in proportion to its duration. Only
    the tail of each stream is kept, for the failure message.
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
        """Run to completion and hand back everything the command wrote."""
        return await self._run(keep_everything=True)

    async def run(self) -> None:
        """Run to completion, keeping only enough output to explain a failure."""
        await self._run(keep_everything=False)

    async def _run(self, keep_everything: bool) -> tuple[str, str]:
        proc = await self.proc
        stdout = _Output(keep_everything)
        stderr = _Output(keep_everything)

        # Both pipes must be drained concurrently, or a chatty one fills its
        # OS buffer and blocks the child while we're still waiting on the
        # other.
        stderr_task = asyncio.create_task(stderr.consume(proc.stderr))
        await self._read_stdout(proc, stdout)
        await stderr_task
        await proc.wait()

        if proc.returncode != 0:
            raise ChildProcessError(f"Command failed - stdout: {stdout.text()}, stderr: {stderr.text()}")

        return stdout.text(), stderr.text()

    async def _read_stdout(self, proc: subprocess.Process, into: _Output) -> None:
        if not (self.on_progress and self.duration_s):
            await into.consume(proc.stdout)
            return

        parser = FfmpegProgressParser(duration_s=self.duration_s, passes=self.passes)
        async for line in proc.stdout:
            into.add(line)
            fraction = parser.feed_line(line.decode(errors="replace"))
            if fraction is not None:
                await self.on_progress(fraction)
