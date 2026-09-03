"""Privileged, well-defined mutations of a Frikanalen media archive.

Installed on the storage host, run by the ingest engine over SSH through sudo,
so that the account ingest logs in as needs no write access to the archive at
all. See README.md for the whole shape of it.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
