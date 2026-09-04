# fk-archive-utils

The ingest engine writes into the media archive on file01 over SSH. This
package is what it writes *through*, so that the account it logs in as needs no
write access to the archive at all. (It does not read through this package, or
over SSH: the archive is exported read-only over NFS and the engine mounts it.)

## Why

An account with write access to `/archive/media` can create, rename and delete
anything anywhere under the archive root — which is to say, a stolen key or a
bug in the engine could destroy every video Frikanalen has ever broadcast, with
nothing between the two to notice.

What the engine actually needs is much smaller than that. Every mutation it
performs goes through one interface (`ArchiveSession` in the ingest
repository), and there are only five of them:

| Mutation | Who asks for it | Reachable over SSH |
| --- | --- | --- |
| **publish** | the upload hook (`original/`), the format producer (`dash/`, `*_thumb/`), the programme-image ingest (`images/`) | yes |
| **trash** | superseding an upload, replacing a rebuilt format | yes |
| **delete-variant** | retiring a temporary, regenerable preview after the full rendition is ready | yes |
| **purge-trash** | an operator, or a timer | no |
| **move** | the one-shot `broadcast/` → `original/` migration | no |

So those five are what this package offers, and nothing else — and only the
first three are things a running ingest engine ever asks for, so only those
three are verbs of `fk-archive`. The sudoers rule ends in a wildcard, which
means what that command *cannot* do is the whole of what the rule withholds.

`delete-variant` is deliberately narrower than `trash`: it can name only one
variant of one video, and an allowlist limits it to derivatives that are both
regenerable from the original and cheap enough that destroying one is not an
incident. Today that means only `dash_preview`. Routine preview retirement
therefore does not bury the exceptional, operator-worthy contents of `.trash/`.
An absent variant is already the requested end state: the command exits 0 with
`"deleted": false`, never exit 4. That idempotence is load-bearing because the
caller deletes before unregistering, and a retry after either half succeeds
must be able to converge.

`move` is on the far side of that line because renaming a file inside a video
happens exactly once per video, ever, as a migration off the directory layout
the previous system used. That is not a reason to give a long-running service
a standing permission to rename things; it is a reason to have an operator run
a migration. See [the migration](#the-broadcast-migration) below.

Two whole-archive operations live here as their own commands rather than as
mutations at all, because their subject is the archive rather than a video:
[garbage collection](#garbage-collection), which reclaims media for videos the
catalogue has dropped, and `fk-archive-purge-trash`, which is the only thing
here that may destroy material that cannot be rebuilt from the original.

## How the pieces fit

```
ingest pod  ──nfs, ro──▶  /archive/media                       (every read)

ingest pod  ──ssh─────▶  ingest@file01
                           forced command: fk-archive-ssh prod
                           └─ "fk-archive …" ──▶ sudo -u archive-manager
                                                   fk-archive prod … (every write)

operator ───────────▶  sudo fk-archive-gc prod --apply
                       sudo fk-archive-purge-trash prod --older-than 30
                       sudo fk-archive-migrate-broadcast prod --apply
```

* **`fk-archive-ssh <profile>`** is the account's forced command. It allows
  `fk-archive` and refuses everything else, an SFTP subsystem request included
  — so a stolen key cannot run arbitrary code as the ingest account either, and
  has no file descriptor on this host to reach for.
* **`fk-archive <profile> {publish,trash,delete-variant}`** performs one mutation
  and prints a JSON object saying what it did. This is the command sudoers
  grants.
* **`fk-archive-gc <profile>`** reclaims media for videos the catalogue no
  longer has. Not granted to the ingest account; `--apply` to act.
* **`fk-archive-purge-trash <profile> --older-than DAYS`** is the destructive
  one. Not in the ingest account's sudoers rule; run it from a timer or by
  hand, `--dry-run` first.
* **`fk-archive-migrate-broadcast <profile>`** is the one-shot migration, and
  is likewise not granted. `--apply` to act.

The archive root is never an argument — it is looked up by profile name in
`/etc/fk-archive-utils/profiles.d/<name>.toml`, and the name is the first
argument, which is what lets one sudoers line pin it:

```
ingest-staging ALL=(archive-manager-staging) NOPASSWD: /usr/bin/fk-archive staging *
```

Staging therefore cannot name production's archive however it is invoked. Had
the root been a `--root` option, that rule would have had to trust the caller
not to.

That line is not left to the deployment to remember. The package installs
`/etc/sudoers.d/fk-archive-utils` as a conffile, carrying a rule for each
profile this project runs; a host with only one of the two accounts gets a rule
that never matches. Without it `fk-archive` is installed but unreachable, and
the failure is quiet in the worst way — sudo asks an SSH session for a password
it has no way to supply, and every publish fails with `sudo: a password is
required`. What the deployment still owns is the profile: a `<name>.toml` that
does not exist means a rule that grants access to an archive nobody named.

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
`.spool/`, are checked against `--size`, and are linked into the published tree
only once all of them have arrived. `--size` is required because it is the only
thing that tells a complete transfer apart from a connection that dropped — a
truncated stream ends exactly like a whole one.

The length is also the only check made on the content, and deliberately so.
SSH already carries its own integrity check over every byte of the transfer,
and a digest the sender computes from the same bytes it then sends agrees with
itself whatever happened to them beforehand — so a hash here would cost a full
pass over every 20 GB original to restate something the transport has already
established.

## Garbage collection

A video deleted from django-api leaves its directory in the archive behind, and
nothing else will ever collect it: everything else this system does is keyed on
a video that exists, and an ingest job belongs to a video, so a deleted one has
no job and never will. What this needs is not a job but a comparison of two
whole collections, run where both can be read:

```bash
sudo fk-archive-gc prod            # lists the orphans and what they hold
sudo fk-archive-gc prod --apply    # moves them into .trash/
```

Two guards, both about blast radius, because this is the one operation here
whose subject is the entire archive:

* **the catalogue read refuses to hand back a partial answer.** Absence from
  the catalogue is read as permission, so half a catalogue would make the
  archive look like garbage in exactly the proportion the read fell short by.
  The endpoint states its own total; a page that comes up short raises rather
  than returning what arrived. The read is also two passes — `proper_import`
  false and then true — because one unfiltered-looking call returns only the
  videos whose ingest *finished*, and everything mid-ingest would otherwise
  look deleted. Unfinished first, so a video that finishes between the two
  passes was already in hand rather than in neither.
* **the share of the archive about to go is checked once, before anything
  moves.** `--max-delete-fraction`, 2% by default. The failure it is really for
  is the archive and the catalogue being different environments: every
  individual decision is then locally correct — that video really is not in
  that catalogue — and only the total is insane. The environment defaulting to
  the archive profile's own name makes that hard to arrange by accident, but
  `--environment` exists, and a genuine mass deletion should stop and ask
  anyway.

Nothing is destroyed. Collecting a video is a rename into `.trash/`, so the
window in which a wrong answer can still be undone is however long the trash is
kept before `fk-archive-purge-trash` runs.

## The broadcast migration

`broadcast/` is what the system before this one called a video's source file.
Nothing has written one for years, and everything since expects the source
under `original/`.

Renaming a file inside a video happens once per video, ever, which is why this
is a command an operator runs rather than something the engine can do: a
one-shot migration is not a reason to give a long-running service a standing
permission to rename archived media.

Like `gc`, the migration is half an archive operation and half a database one —
moving the file without retagging the row that names it would leave the
catalogue pointing at nothing — so it talks to django-api, using the token
`fk-cli` logged in with:

```bash
sudo fk-archive-migrate-broadcast prod            # plan only; changes nothing
sudo fk-archive-migrate-broadcast prod --apply
```

Both tools that read the catalogue work the same way, and it is worth stating
once. Run them with plain `sudo`: root is what can read the operator's
`~/.frikanalen.yaml`, and each reads the token and then permanently becomes the
profile's manager account before opening the archive, so the directories it
creates are owned by the account that has to write into them afterwards.

The fk-cli environment defaults to **the archive profile's name**, not to the
`environment:` the config file currently selects. Pairing the production
archive with whichever catalogue an operator last pointed fk-cli at is the one
mistake here that a rename cannot undo, so the two are tied together unless
`--environment` separates them. With no token for that environment, they say to
log in with `fk-cli` and stop.

It decides per video, and prints a line for each:

| What it finds | What it does |
| --- | --- |
| not in the catalogue | nothing — the backfill's `gc` takes the whole video |
| no files in `broadcast/` | nothing |
| `original/` already holds the source | trash `broadcast/`, drop the rows that named it |
| `broadcast/` holds media no row claims | nothing, and says so — moving it would be guessing |
| otherwise | move each file to `original/`, retag the rows, trash the emptied directory |

Trash before unregister, so a failure between them leaves media removed but
still recorded rather than the reverse. Move and retag before trashing the
directory, so nothing is ever recorded at a path that does not yet hold it. One
video failing does not stop the run.

**Delete all of this when it has finished.** `migrate_broadcast.py`,
`operations.move`, the entry point and the `python3-yaml` dependency exist for
it and nothing else, and a migration left in the code after it has finished
migrating reads like a rule about how the archive works.

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
* **Destroying only what can be rebuilt.** Trash is a rename, and each trash
  operation gets its own exclusively-created stamp directory, which is also
  what makes the rename into it safe without a no-clobber rename.
  `delete-variant` may unlink only an explicitly allowed cheap, regenerable
  derivative. Only `fk-archive-purge-trash` may destroy anything else, and it
  is withheld from the ingest account.

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
| 7 | django-api could not be reached or would not answer |
| 8 | the catalogue could not be read in full — nothing was collected |
| 9 | more of the archive is unaccounted for than a sweep will act on alone |

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

**Upgrading to the version that dropped the SFTP half is not order-free.** The
forced command in this version refuses an SFTP subsystem request, and an ingest
that still reads over SFTP has no other way to see the archive — so roll the
engine onto its NFS mount first, confirm it is reading from there, and install
this afterwards. Going the other way takes every read out at once.

## Developing

```
uv sync
uv run pytest
uv run ruff check
```

Every command an SSH session can reach imports the standard library and nothing
else, on purpose: those run as the account that owns every video Frikanalen
has, under sudo, and the dependency list is part of that attack surface. The
single dependency, PyYAML, is imported inside the one function of the one-shot
migration that reads `~/.frikanalen.yaml`, and goes when the migration does.
