"""A throwaway in-process SSH/SFTP server, for exercising the SSH archive store.

Serving the real filesystem (rather than a chroot) keeps the paths under test
identical to the absolute paths ingest uses against file01.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncssh


@dataclass(frozen=True)
class SshServerFixture:
    host: str
    port: int
    username: str
    client_key_file: Path
    known_hosts_file: Path


def _write_key_pair(directory: Path, name: str) -> tuple[Path, Path]:
    key = asyncssh.generate_private_key("ssh-ed25519")

    private_key_file = directory / name
    private_key_file.write_bytes(key.export_private_key())
    private_key_file.chmod(0o600)

    public_key_file = directory / f"{name}.pub"
    public_key_file.write_bytes(key.export_public_key())

    return private_key_file, public_key_file


@asynccontextmanager
async def run_ssh_server(directory: Path, username: str = "ingest") -> AsyncIterator[SshServerFixture]:
    host_key_file, host_public_key_file = _write_key_pair(directory, "host_key")
    client_key_file, client_public_key_file = _write_key_pair(directory, "client_key")

    authorized_keys_file = directory / "authorized_keys"
    authorized_keys_file.write_bytes(client_public_key_file.read_bytes())

    server = await asyncssh.listen(
        host="127.0.0.1",
        port=0,
        server_host_keys=[str(host_key_file)],
        authorized_client_keys=str(authorized_keys_file),
        sftp_factory=True,
    )

    port = server.get_port()

    # A non-default port is spelled [host]:port in known_hosts.
    known_hosts_file = directory / "known_hosts"
    known_hosts_file.write_text(f"[127.0.0.1]:{port} {host_public_key_file.read_text().strip()}\n")

    try:
        yield SshServerFixture(
            host="127.0.0.1",
            port=port,
            username=username,
            client_key_file=client_key_file,
            known_hosts_file=known_hosts_file,
        )
    finally:
        server.close()
        await server.wait_closed()
