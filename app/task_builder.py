import asyncio
import shlex

from app.runner import Task


def TKB(command: str) -> Task:
    """Task Builder for running subprocesses asynchronously. The name is a reference to RSX-11M+ :)

    This function takes a command string, splits it into arguments, and creates a Task
    that can be executed asynchronously. It uses asyncio's subprocess capabilities to
    run the command in a subprocess, capturing both stdout and stderr.

    Args:
        command (str): The shell command to run.

    """
    return Task(
        asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
