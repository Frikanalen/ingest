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
from typing import TypedDict

TusdMessageLog = TypedDict("TusdMessageLog", {"msg": str, "extra": dict | None})


# Matches leading timestamp, e.g. "2025/07/23 14:34:43.763302 "
_ts_prefix = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{6})\s+")


def structured_log_message(message: str) -> TusdMessageLog:
    parts = shlex.split(message)
    kv = {}
    for part in parts:
        if "=" not in part:
            raise ValueError("Not a structured line")
        k, v = part.split("=", 1)
        kv[k] = v
    return {"msg": kv.__repr__(), "extra": kv}


def _split_timestamp_and_message(line: str) -> tuple[str, str]:
    match = _ts_prefix.match(line)
    assert match, f"Invalid log line format - must start with timestamp: {line!r}"
    timestamp = match.group(1)
    message = line[match.end() :].strip()
    return timestamp, message


def parse_tusd_line(line: str) -> TusdMessageLog:
    """
    Parses a log line emitted by the tusd server to extract structured logging
    information. If the message doesn't conform to the expected structure, it
    returns a fallback dictionary containing the raw message and an 'extra' field
    set to None.

    Parameters:
    line: str
        A single line of log output from the tusd server.

    Returns:
    TusdMessageLog
        Returns a structured log message if parsing is successful. Otherwise,
        returns a dictionary with the message and an 'extra' field set to None.

    Raises:
    ValueError
        If the logged line does not start with a timestamp as expected

    Example:
        logger.info(**parse_tusd_line(line))
    """
    [_, message] = _split_timestamp_and_message(line)

    try:
        return structured_log_message(message)
    except ValueError:
        return {"msg": message, "extra": None}
