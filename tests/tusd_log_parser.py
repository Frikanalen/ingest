"""
tusd_log_parser.py

Utilities for parsing log lines emitted by the tusd upload server.

This module supports both structured logs of the form:
    2025/07/23 14:34:43.763302 level=INFO event=ChunkWriteComplete ...

And unstructured logs like:
    2025/07/23 14:52:10.069053 Using 0.0.0.0:8080 as address to listen.
"""

import shlex
import re
from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class TusdStructuredLog:
    entry_type: Literal["structured"]
    timestamp: str
    fields: dict[str, str]


@dataclass(frozen=True)
class TusdMessageLog:
    entry_type: Literal["plaintext"]
    timestamp: str
    message: str


# Matches leading timestamp, e.g. "2025/07/23 14:34:43.763302 "
_ts_prefix = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{6})\s+")


def parse_tusd_line(line: str) -> Union[TusdStructuredLog, TusdMessageLog]:
    """
    Parse a single log line emitted by tusd.

    Args:
        line: The raw log line, including the timestamp prefix.

    Returns:
        A dict with:
          - type: "structured" for key=value logs, or "message" for plain text logs
          - timestamp: The timestamp string
          - fields/message: Parsed key-value pairs or unstructured message text

    Raises:
        ValueError: If the line does not begin with a valid timestamp.
    """
    match = _ts_prefix.match(line)
    if not match:
        raise ValueError(f"Line does not start with timestamp: {line!r}")

    timestamp = match.group(1)
    rest = line[match.end() :].strip()

    try:
        parts = shlex.split(rest)
        kv = {}
        for part in parts:
            if "=" not in part:
                raise ValueError("Not a structured line")
            k, v = part.split("=", 1)
            kv[k] = v
        return TusdStructuredLog(entry_type="structured", timestamp=timestamp, fields=kv)
    except ValueError:
        return TusdMessageLog(entry_type="plaintext", timestamp=timestamp, message=rest)
