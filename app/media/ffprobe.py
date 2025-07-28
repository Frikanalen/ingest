import asyncio
import logging
from pathlib import Path

from app.media.ffprobe_schema import FfprobeOutput

logger = logging.getLogger(__name__)


async def _run_ffprobe(filepath: Path) -> str:
    cmd = ["/usr/bin/ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", str(filepath)]
    logger.debug("Running ffprobe command: %s", " ".join(cmd))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()}")

    return stdout.decode()


async def do_probe(filepath: Path) -> FfprobeOutput:
    data = await _run_ffprobe(filepath)
    logger.debug("Validating ffprobe output against JSON Schema: %s", data)
    return FfprobeOutput.model_validate_json(data)


if __name__ == "__main__":
    import sys

    async def main():
        result = await do_probe(Path(sys.argv[1]))
        print(result)

    asyncio.run(main())
