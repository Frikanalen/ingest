"""A throwaway in-process SSH server, shaped like the storage host.

Two halves, because the storage host has two: an SFTP server that refuses every
write, and `fk-archive`, which performs the mutations the engine is allowed to
ask for. Between them they are the whole of what an ingest key can reach on
file01 -- see `archive-utils/src/fk_archive_utils/ssh_command.py`, which is the
forced command this stands in for.

The mutating half runs the real `fk_archive_utils`, not an imitation of it. The
two packages ship separately and agree only on a command line, an exit code and
a line of JSON, so a test double here would be free to keep agreeing with an
engine that had drifted -- which is the one failure this arrangement cannot
afford, since it would surface as an archive refusing every upload.

Serving the real filesystem (rather than a chroot) keeps the paths under test
identical to the absolute paths ingest uses against file01.
"""

import io
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, redirect_stderr
from dataclasses import dataclass
from pathlib import Path

import asyncssh
from asyncssh.constants import FXF_APPEND, FXF_CREAT, FXF_TRUNC, FXF_WRITE

from fk_archive_utils import cli

#: The exit code `fk-archive-ssh` leaves with when a key asks for something it
#: may not run. Mirrored rather than imported so this fixture keeps saying what
#: the forced command says even if the two ever part company.
NOT_PERMITTED = 2


@dataclass(frozen=True)
class SshServerFixture:
    host: str
    port: int
    username: str
    client_key_file: Path
    known_hosts_file: Path


class ReadOnlySftpServer(asyncssh.SFTPServer):
    """The `sftp-server -R` half, which is where every read goes.

    Refusing writes is the property under test, not an economy: the engine is
    supposed to have no way to alter the archive except by asking, and a
    fixture that quietly allowed an SFTP write would let a rename creep back
    into the engine and still pass.
    """

    #: Enough to fail an open that intends to write anything, in the SFTPv3
    #: flags asyncssh's client sends.
    _WRITE_FLAGS = FXF_WRITE | FXF_APPEND | FXF_CREAT | FXF_TRUNC

    def _refuse(self, *args: object, **kwargs: object) -> None:
        raise asyncssh.SFTPPermissionDenied("the archive is exported read-only to this account")

    def open(self, path: bytes, pflags: int, attrs: asyncssh.SFTPAttrs) -> object:
        if pflags & self._WRITE_FLAGS:
            self._refuse()
        return super().open(path, pflags, attrs)

    write = _refuse
    setstat = _refuse
    lsetstat = _refuse
    fsetstat = _refuse
    remove = _refuse
    mkdir = _refuse
    rmdir = _refuse
    rename = _refuse
    posix_rename = _refuse
    symlink = _refuse
    link = _refuse


def _write_profile(directory: Path, archive_root: Path, name: str) -> Path:
    """The profile `fk-archive` looks its archive root up in.

    `manager` names the account the storage host sudoes to. Nothing in the
    fixture sudoes anywhere -- the tests run as one user against a temporary
    directory -- but the field is required, and naming it here is what keeps
    the profile the same shape as a real one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.toml").write_text(
        f'root = "{archive_root}"\nmanager = "archive-manager"\n',
        encoding="utf-8",
    )
    return directory


async def _serve_command(process: asyncssh.SSHServerProcess, profile_dir: Path, profile: str) -> None:
    """Dispatch one exec request the way the forced command would.

    The profile is supplied here rather than taken from the request, exactly as
    `fk-archive-ssh` supplies it: it is what stops a key naming an archive it
    was not issued for, so a fixture that let the client choose would be
    testing a different arrangement.

    Both streams are carried back, because the client reads both: stdout is the
    JSON saying what was done, and stderr is the sentence saying what was
    refused. Under sudo those are the channel's own descriptors; here `run()`
    takes a stdout and writes its refusals to `sys.stderr`, so stderr is
    captured by redirecting it -- which is why the call is made inline rather
    than in a thread. `redirect_stderr` is process-wide, and running it without
    an await inside is what keeps it from reaching anything but this command.
    """
    tokens = shlex.split(process.command or "")

    if not tokens or Path(tokens[0]).name != "fk-archive":
        process.stderr.write(f"fk-archive-ssh: {process.command!r} is not something this key may run\n".encode())
        process.exit(NOT_PERMITTED)
        return

    # Drained into memory first: `run()` wants a stream it can read in chunks,
    # and the channel is not one. Test payloads are small; the real command
    # reads the socket itself.
    payload = io.BytesIO(await process.stdin.read())
    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stderr(stderr):
        status = cli.run([profile, *tokens[1:]], stdin=payload, stdout=stdout, profile_dir=profile_dir)

    process.stdout.write(stdout.getvalue().encode())
    process.stderr.write(stderr.getvalue().encode())
    process.exit(status)


def _write_key_pair(directory: Path, name: str) -> tuple[Path, Path]:
    key = asyncssh.generate_private_key("ssh-ed25519")

    private_key_file = directory / name
    private_key_file.write_bytes(key.export_private_key())
    private_key_file.chmod(0o600)

    public_key_file = directory / f"{name}.pub"
    public_key_file.write_bytes(key.export_public_key())

    return private_key_file, public_key_file


@asynccontextmanager
async def run_ssh_server(
    directory: Path,
    archive_root: Path,
    username: str = "ingest",
    profile: str = "test",
) -> AsyncIterator[SshServerFixture]:
    host_key_file, host_public_key_file = _write_key_pair(directory, "host_key")
    client_key_file, client_public_key_file = _write_key_pair(directory, "client_key")

    authorized_keys_file = directory / "authorized_keys"
    authorized_keys_file.write_bytes(client_public_key_file.read_bytes())

    profile_dir = _write_profile(directory / "profiles.d", archive_root, profile)

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        await _serve_command(process, profile_dir, profile)

    server = await asyncssh.listen(
        host="127.0.0.1",
        port=0,
        server_host_keys=[str(host_key_file)],
        authorized_client_keys=str(authorized_keys_file),
        sftp_factory=ReadOnlySftpServer,
        process_factory=process_factory,
        # Binary, because half of what goes up one of these channels is a video
        # file. The SFTP half sets this for itself regardless.
        encoding=None,
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
