from dataclasses import dataclass, field
from pathlib import Path

from tests.utils.get_free_port import get_free_port


@dataclass(frozen=True)
class TusdHttpHookConfig:
    url: str
    enabled_hooks: list[str] | None = None


@dataclass(frozen=True)
class TusdConfig:
    upload_dir: Path
    binary_path: str
    hook_config: TusdHttpHookConfig | None = None
    port: int = field(default_factory=get_free_port)


def _get_hook_config(hook_config: TusdHttpHookConfig) -> list[str]:
    cmd = [
        "-hooks-http",
        hook_config.url,
    ]

    if hook_config.enabled_hooks is not None:
        cmd.extend(["-hooks-enabled-events", ",".join(hook_config.enabled_hooks)])

    return cmd


def build_tusd_command(config: TusdConfig):
    """Build the complete command line for tusd"""
    cmd = [
        config.binary_path,
        "-upload-dir",
        str(config.upload_dir.absolute()),
        "-hooks-http-forward-headers",
        "Cookie",
        "-port",
        str(config.port),
    ]
    if config.hook_config:
        cmd.extend(_get_hook_config(config.hook_config))

    return cmd
