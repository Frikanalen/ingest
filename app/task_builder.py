"""RSX-11M for life"""

from asyncio import create_subprocess_shell, subprocess

from app.runner import Task


def build_task(shell_command: str) -> Task:
    """Creates a Task to run a shell command asynchronously.

    Args:
        shell_command (str): The shell command to run.
    """
    return Task(create_subprocess_shell(shell_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
