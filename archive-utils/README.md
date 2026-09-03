# fk-archive-utils

The ingest engine writes into the media archive on file01 over SSH. This
package is what it writes *through*, so that the account it logs in as needs no
write access to the archive at all.

## Why

Before this, ingest held an SFTP account with write access to `/archive/media`.
That account could create, rename and delete anything anywhere under the
archive root — which is to say, a stolen key or a bug in the engine could
destroy every video Frikanalen has ever broadcast, and nothing between the two
would have noticed.

What the engine actually needs is much smaller than that. Every mutation it
performs goes through one interface (`ArchiveSession` in the ingest
repository), and there are only four of them:

| Mutation | Who asks for it | What it does |
| --- | --- | --- |
| **publish** | the upload hook (`original/`), the format producer (`dash/`, `*_thumb/`), the programme-image ingest (`images/`) | put a file at `<video-id>/<category>/<filename>` |
| **move** | the backfill's legacy `broadcast/` → `original/` migration | rename a file inside one video |
| **trash** | superseding an upload, collecting a video the catalogue dropped, replacing a rebuilt format | rename a video or one of its directories into `.trash/` |
| **purge-trash** | an operator, or a timer | delete trash entries past a given age |

So those four are what this package offers, and nothing else. There is no verb
that overwrites, none that deletes from the published tree, and the one that
deletes at all ships as a separate command the ingest account is not granted.

## How the pieces fit

```
ingest pod  ──ssh──▶  ingest@file01
                        │  forced command: fk-archive-ssh prod
                        ├─ SFTP subsystem ──▶ sftp-server -R      (every read)
                        └─ "fk-archive …"  ──▶ sudo -u archive-manager
                                                  fk-archive prod … (every write)
```

* **`fk-archive-ssh <profile>`** is the account's forced command. It allows a
  read-only SFTP server and `fk-archive`, and refuses everything else — so a
  stolen key cannot run arbitrary code as the ingest account either.
* **`fk-archive <profile> {publish,move,trash}`** performs one mutation and
  prints a JSON object saying what it did. This is the command sudoers grants.
* **`fk-archive-purge-trash <profile> --older-than DAYS`** is the destructive
  one. Not in the ingest account's sudoers rule; run it from a timer or by
  hand, `--dry-run` first.

The archive root is never an argument — it is looked up by profile name in
`/etc/fk-archive-utils/profiles.d/<name>.toml`, and the name is the first
argument, which is what lets one sudoers line pin it:

```
ingest-staging ALL=(archive-manager-staging) NOPASSWD: /usr/bin/fk-archive staging *
```

Staging therefore cannot name production's archive however it is invoked. Had
the root been a `--root` option, that rule would have had to trust the caller
not to.

## Publishing takes the bytes on stdin

`fk-archive prod publish 12/original/a.mov --size 4823` reads the file from
standard input. It does not move a file the caller left in a spool directory,
and that is the decision the whole package turns on: **a spool the ingest
account can write to is a spool whose files the ingest account owns**, and a
rename does not change ownership — so every file it ever published would stay
writable by it, which is exactly the general write permission this exists to
remove.

Streaming through the privileged command instead means the archive account
creates the file itself. Ingest needs no writable directory on file01.

The staging is still real, just on the far side of the fence: bytes land in
`.spool/`, are checked against `--size` (and `--sha256`, if given), and are
linked into the published tree only once all of them have arrived. `--size` is
required because it is the only thing that tells a complete transfer apart from
a connection that dropped — a truncated stream ends exactly like a whole one.

## What the tools refuse

* **Paths outside the archive's namespace.** A publish destination is
  `<video-id>/<category>/<filename>` and nothing else; a trash target is a video
  or one directory inside one. There is no valid path four components deep, so
  "a filename, not a path" is enforceable rather than aspirational. Video ids
  are digits with no leading zeros; `.spool` and `.trash` cannot be named.
* **Symbolic links.** Nothing here hands a multi-component path to the kernel.
  The root is opened once and each component is opened relative to the last with
  `O_NOFOLLOW`, and every mutation is performed with `dir_fd` against the
  directory reached that way — so there is no window between checking a path and
  using it.
* **Overwriting.** Publish and move `link(2)` the new name into place and unlink
  the old, because `link` fails with `EEXIST` where `rename` would replace
  silently. The archive is exported read-only to the playout hosts; a file
  swapped under a reader is worse than a refusal.
* **Deleting.** Trash is a rename. Only `fk-archive-purge-trash` unlinks
  anything, and each trash operation gets its own exclusively-created stamp
  directory, which is also what makes the rename into it safe without a
  no-clobber rename.

## Exit codes

The only thing that survives an SSH command invocation is the exit status, so
each failure the caller acts on differently gets its own.

| Code | Meaning |
| --- | --- |
| 0 | done |
| 1 | something else went wrong (`ENOSPC`, `EROFS`, …) |
| 2 | the invocation was wrong — a malformed path, a missing option |
| 3 | something is already at the destination |
| 4 | the path is not in the archive |
| 5 | the bytes that arrived are not the bytes that were promised — retryable |
| 6 | the profile is missing or unusable — a fault on this host |

## Installing

Built as a Debian package for trixie and installed by
`roles/fk_archive_utils` in the infra repository. To build it yourself on a
Debian box:

```
sudo apt build-dep .          # or: debhelper dh-python pybuild-plugin-pyproject python3-all
./build-deb.sh 1.2.3
```

Sample configuration is installed to
`/usr/share/doc/fk-archive-utils/examples/`.

## Developing

```
uv sync
uv run pytest
uv run ruff check
```

No runtime dependencies, on purpose: this runs as the account that owns every
video Frikanalen has, under sudo, on the storage host. The dependency list is
part of the attack surface, and the standard library covers all of it.
