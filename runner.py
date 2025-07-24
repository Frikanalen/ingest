import asyncio
import logging


class Runner:
    @classmethod
    async def run(cls, command: list[str]):
        logging.info("Running: %s", command)

        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        assert proc.returncode == 0, f"Command {command} failed, stderr: {stderr.decode()}"
