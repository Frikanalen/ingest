# Ingest

Ingest handles tusd uploads for Frikanalen by validating metadata, archiving source files, generating derivative media, and updating the Django API. A worker converges any video it is handed toward the same declaration of what a video should have, so a fresh upload and one archived years ago are the same job — see [Reconciling the catalogue](#reconciling-the-catalogue). Deciding *which* videos to hand it is an operator's, and is `fk archive` in fk-cli.

It exposes these application endpoints:

- `POST /tusdHooks/` handles tusd's `pre-create` and `post-finish` hooks. `pre-create` validates the upload metadata and assigns its storage path; `post-finish` starts the ingest job.
- `GET /internal/isAlive` is the health check.
- `GET /ingest-api/formats` reports what this deployment can build: each desired variant, the revision of the template currently producing it, and the image answering. It is the only endpoint reachable from outside the cluster — see [Publishing the format revisions](#publishing-the-format-revisions).
- `GET /watchFolder/tusFiles` and `GET /watchFolder/archive` stream directory listings as server-sent events for debugging. Filesystem changes do not start ingest jobs. **Only mounted when `FK_DEBUG` is set** — see [Debug mode](#debug-mode).

For a completed upload the hook checks the media with FFprobe, copies the source file from `FK_TUSD_DIR` to `<archive>/<video-id>/original/<filename>`, registers it along with the video's duration and upload time, and queues an ingest job. Then it returns. The uploaded file is removed from `FK_TUSD_DIR` at that point, because the archive now holds the same bytes and the queued job reads from there; a failure before the file is archived leaves it in place for a retry.

Everything after that is a [queue worker](#queue-workers)'s: loudness, framerate, and every derived format — `<archive>/<video-id>/large_thumb/<stem>.jpg`, `<archive>/<video-id>/med_thumb/<stem>.jpg`, `<archive>/<video-id>/small_thumb/<stem>.jpg` and `<archive>/<video-id>/dash/`. **A member's upload therefore depends on the worker pool being scaled above zero.** Nothing in ingest notices a job nobody claims.

An upload to a video that already has one **supersedes it**. Before the new original is archived, every directory under `<archive>/<video-id>/` except `images/` is trashed and every `videofile` row for the video is dropped, so the worker rebuilds the lot from the file that just arrived. Nothing is deleted — trashing is a rename into `.trash/`, purged separately — and programme images are left alone, because they are registered in another table and describe the programme rather than its media.

This is what makes correcting a video possible at all: there is no other way to replace the file behind a video id without abandoning the id, its schedule slots and every link to it. It is deliberately not gated on the import having finished, since the mistake a member needs to undo — the wrong cut, the wrong language track — is usually one they discover after it did. It is also what keeps `original/` holding exactly one file, which every later job depends on: a second upload under a different name used to land beside the first and leave the video permanently unprocessable.

The split is the upload volume. It is `ReadWriteOnce`, which pins the hook's pod to a single replica; a worker mounts no upload volume, which is why the pool scales. Archiving the original is the step that turns a file only one pod can see into a file every worker can, so it is the last thing that has to happen in the request.

FFmpeg always reads a file from local disk and writes to local scratch space; only finished files are handed to the archive. That is what lets the archive live on another host.

### Programme images

Programme images use the same tusd spool and upload token as video files. Their
upload metadata sets `uploadKind=program_image` and includes an `imageRole`.
After upload, ingest verifies that the file is a single-frame JPEG, PNG or WebP
of at most 10 MB, reads its real format and dimensions, and publishes it as
`<video-id>/images/<upload-id>.<extension>`. It registers that archive-relative
path with Django only after the archive rename succeeds. A failed registration
leaves the tusd copy available for a retry; registration is idempotent.

### Debug mode

`FK_DEBUG=true` turns on three things a deployment should not have, and nothing else: the
`/watchFolder` endpoints, the directory observer that feeds them, and FastAPI's own debug mode, which
returns a traceback to whoever provoked the error. It also drops logging from `INFO` back to `DEBUG`.
It defaults to off.

The observer is the reason this is a switch rather than a comment. It is a `watchdog`
`PollingObserver` — no inotify, so it walks the tree and stats every entry, once a second, forever, in
a thread. In production that tree is the 200 GiB upload volume shared with tusd, inside the pod
serving tusd's hooks. That pod no longer transcodes, so a permanent recursive stat of the whole
volume is now a large share of everything it does.

## Formats

Each format in `app/templates/` is a command with a YAML header, and each gets a scratch directory of its own. Whatever the command leaves in that directory is archived; anything that must not be archived, like a two-pass log, goes in `scratch_dir` instead. The header names the format's primary output — the one file registered with the Django API, and the last one published — either as an extension applied to the source file's stem (`output_file_extension`) or as a fixed name (`output_file_name`).

Publishing order matters because the archive is exported read-only to the playout hosts: the primary output goes last, so a manifest is never readable before the media it references has arrived.

### Thumbnails

`large_thumb` (720px wide), `med_thumb` (320px) and `small_thumb` (120px) are single frames pulled a quarter of the way into the video, each a single-pass FFmpeg invocation. Three sizes exist so a consumer — a detail page, a grid of cards, a compact list row — can pick the one closest to what it actually renders, rather than every context fetching the 720px original and scaling it down in the browser.

### DASH

`dash` is an adaptive VP9/Opus ladder — 1080p, 720p and 360p, none of them upscaled past the source — played back over MSE by a browser-side player. It is one FFmpeg invocation: the source is decoded once, padded to 16:9, and split into the three renditions in a single pass.

Segments live inside one file per representation, addressed by byte range from the manifest (`-single_file 1`), rather than as a file each. A one-hour video is five files instead of several thousand, which is what makes DASH viable over a remote archive at all: publishing is one privileged command per file, so a ladder of segments would be a command apiece. The cost is a manifest that grows with duration, by roughly 120 KB per hour; it compresses well, and if it ever becomes a problem the fix is rewriting the manifest into on-demand `SegmentBase` form.

Keyframes are pinned to wall-clock time rather than a GOP length in frames, so renditions stay aligned for switching whatever frame rate is uploaded. A source with no audio track gets no audio adaptation set, since an adaptation set with no representation in it is not valid DASH.

Serving it needs HTTP range requests, CORS, and `application/dash+xml` on the `.mpd`; none of that is ingest's side of the job.

## Archive

The archive is either a local directory ingest writes to, or the deployed one, which it **reads off a read-only mount and mutates by asking the host that owns it**. Setting `FK_ARCHIVE_HOST` selects the latter; `FK_ARCHIVE_DIR` is then where that mount appears in the container.

Reading and writing arriving by different routes is the whole shape of it, and it is why `ArchiveReader` and `ArchiveSession` are separate interfaces in [`base.py`](app/archive_store/base.py). A listing, a `stat` and one copy of the original per job are filesystem calls against the mount, so nothing on the read path needs the archive host to be up, or a key, or a connection at all. Everything that changes the archive is a request to a named command — see [Writing without write access](#writing-without-write-access).

| Setting | Meaning |
| --- | --- |
| `FK_ARCHIVE_DIR` | Where the archive is. A local directory, or — with `FK_ARCHIVE_HOST` — where the archive is mounted read-only in this container. |
| `FK_ARCHIVE_HOST` | Archive host, asked to perform every mutation. Unset means archive locally. |
| `FK_ARCHIVE_PORT` | SSH port, default `22`. |
| `FK_ARCHIVE_USERNAME` | SSH user, default `ingest`. |
| `FK_ARCHIVE_PRIVATE_KEY_FILE` | SSH private key to authenticate with. |
| `FK_ARCHIVE_KNOWN_HOSTS_FILE` | `known_hosts` file used to verify the archive host. |
| `FK_ARCHIVE_FALLBACK_DIR` | Local directory used when the SSH credentials are missing, default `./archive`. |
| `FK_ARCHIVE_REQUIRED` | Fail startup instead of falling back. Set this in deployments. |
| `FK_WORK_DIR` | Local scratch space for transcoding. Defaults to the system temporary directory. |

Files are staged below the archive's `.spool/` directory and only appear at
their destination once complete, so an interrupted transfer cannot leave a
truncated file that later looks like a finished one. In a deployment that
staging happens on the far side of the connection — see below.

**The mount must be read-only, and it must be a mount of the directory the
`fk-archive` profile on the storage host calls its root.** Ingest warns at
startup if it finds the archive mounted read-write, since that hands back
exactly the write access the rest of this arrangement exists to remove. It
refuses to start if the archive is not mounted at all: an unmounted volume
reads as an archive holding nothing, every video then observes as having no
media, and a worker would set about rebuilding the lot.

Both SSH credentials must be given explicitly — ingest will not reach for the running user's `~/.ssh`, and it never disables host key verification. If either is missing, ingest logs a warning and archives to `FK_ARCHIVE_FALLBACK_DIR` instead, so you can run it locally without setting up SSH at all. **Set `FK_ARCHIVE_REQUIRED=true` anywhere that actually archives over SSH**: otherwise a secret that fails to mount leaves ingest quietly writing to scratch space, where files are lost on restart.

### Writing without write access

**The SSH account has no write access to the archive, and no read access
either.** Reads do not go over the connection at all — they come off the mount
— and every mutation is a request to [`fk-archive`](archive-utils/), a command
the storage host runs under sudo as the account owning the media. A stolen key
can therefore ask for a file to be published or a directory to be trashed, and
cannot do anything else on that host at all.

The two mutations ingest performs are the two verbs that command has:

| Mutation | Who asks for it | How |
| --- | --- | --- |
| publish | the upload hook (`original/`), the format producer (`dash/`, `*_thumb/`), the programme-image ingest (`images/`) | `fk-archive publish <video-id>/<category>/<file> --size N`, with the bytes on stdin |
| trash | superseding an upload, replacing a rebuilt format | `fk-archive trash <video-id>[/<category>]` |

Publishing sends the file up the command's standard input rather than into a
spool ingest fills, because a spool ingest can write to is a spool whose files
ingest *owns* — and a rename does not change that, so every file it ever
published would stay writable by it. The far end stages the bytes in `.spool/`,
checks them against the length promised in `--size`, and links them into the
published tree only once all of them have arrived. `--size` is what tells a
complete transfer apart from a connection that dropped: a truncated stream ends
exactly like a whole one.

`.spool/` and the trash therefore belong to the archive account, and ingest has
no way to sweep either. Its side of the deal is [`fk_archive.py`](app/archive_store/fk_archive.py),
which is the whole of what crosses the boundary: how a request is spelled, and
how the exit code and the line of JSON that come back are read.

**The archive root is not an argument to any mutation** — `fk-archive` looks it
up by profile name, which is what lets one sudoers line pin it, and what stops
staging naming production's archive however it is invoked. `FK_ARCHIVE_DIR` no
longer has to spell the same string as `root` in
`/etc/fk-archive-utils/profiles.d/<profile>.toml`, as it did when reads went
over SFTP: it has to be a *mount of* that directory, which is the chart's
business rather than something two settings can be compared to check. Point
them at different trees and ingest publishes files it then cannot find.

Renaming archived media is not among the mutations. `ArchiveSession` has no
`move`, and neither does `fk-archive`: moving a file inside a video happens once
per video, ever, as a migration off the layout the previous system used, and
that is [`fk-archive-migrate-broadcast`](archive-utils/README.md#the-broadcast-migration),
run by an operator. Nor is deleting: only `fk-archive-purge-trash` unlinks
anything, and it ships as a separate command precisely so the ingest account's
sudoers rule can leave it out.

The package also holds the two whole-archive operations that are nobody's job to
queue: `fk-archive-gc`, which reclaims media for videos the catalogue has
dropped, and the broadcast migration above. Both compare the archive against the
catalogue and are run by an operator on the storage host.

It is built and released by
[`.github/workflows/archive-utils.yml`](.github/workflows/archive-utils.yml) and
installed by `roles/fk_archive_utils` in the infra repository.
`archive-utils/README.md` has the design and the exit-code table; the infra
role's README has the order to deploy it in.

## Reconciling the catalogue

Ingest is not only a hook handler. What a video is *supposed* to have is declared in one place — `DESIRED_FORMATS` in `app/formats.py`, and the revision each template in `app/templates/` declares — and both paths that produce media converge on it: a fresh upload, and a video that has been in the archive for years.

They converge on it by running the same code. Once the hook has archived the original, an upload is a video like any other: a worker observes it, plans the difference against the desired state, and applies the plan — the same `CHORES` whatever put the job on the queue. The upload path holds no list of formats of its own, so a format added to `DESIRED_FORMATS` or a template whose revision moves reaches both paths or neither. It is also how a freshly uploaded video gets its `framerate`, which the hook works out anyway for DASH segmentation and previously had nowhere to put.

**Deciding which videos need converging is not ingest's.** It is an operator's, done from a terminal, and it lives in fk-cli — see [Queueing the work](#queueing-the-work). Ingest converges the video it was handed; nothing in `app/` looks at the catalogue as a whole or puts anything but an upload on the queue.

That matters because "this video has DASH" and "this video has *current* DASH" are different statements. Each template carries a `revision`, and `profileRevision` on the videofile row records which one produced the file. Revisions number from 1, so 0 means "registered before any of this was recorded" — which is what every pre-existing row reads as, and therefore as stale. Changing a profile is then: edit the template, bump its revision, and everything built by the old one becomes due for a rebuild without anybody keeping a list.

### Chores

| Chore | What it settles |
| --- | --- |
| `metadata` | `duration`, `framerate` and R.128 loudness, re-derived from the original |
| `formats` | Derivatives that are missing, or built by a superseded profile |

That is the whole set, and there is deliberately no second list for one caller to run instead: `CHORES` in `app/converge/chores.py` is what it means to converge a video. A chore that reached one path and not the other is precisely the drift this arrangement exists to prevent, which is why the questions the queue side asks are answered from here — the desired formats and their revisions are published at [`/ingest-api/formats`](#publishing-the-format-revisions) rather than reimplemented by whoever is deciding what to queue.

Both are about a video that exists, which is the constraint that keeps every chore queueable: an ingest job belongs to a video, so anything a chore can decide is something a worker can be handed. Media belonging to a video the catalogue has *deleted* fits neither half of that, and is [`fk-archive-gc`](archive-utils/README.md#garbage-collection)'s on the storage host.

Settling whether a video's source lives in `original/` or in the `broadcast/` directory the previous system used is not among them, and is not a chore: it happens once per video, ever, and is [`fk-archive-migrate-broadcast`](archive-utils/README.md#the-broadcast-migration) on the storage host. A video still in the old shape reads here as having no registered original — the `formats` chore says so in a note and derives nothing, which is the right answer until the migration has run.

Each is a pure function from an observed `VideoState` to the actions that would close the gap, so the awkward cases — a format registered twice, media nothing claims, a video whose source is nowhere the catalogue says it is — are unit tests rather than fixtures.

**The database decides.** No chore invents a videofile row from a file it found, and none deletes a row because a file is missing. The first lets the archive overrule the catalogue; the second destroys the only remaining evidence of an incident. Both are reported instead, as notes nothing acts on.

**Nothing here deletes.** `ArchiveSession` has no way to destroy archived media: removing something is a rename into `.trash/<timestamp>/<original path>`, so putting it back is the reverse rename. Purging is a separate act, and lives on the storage host as `fk-archive-purge-trash`.

### Queueing the work

Two commands, one per chore, in [fk-cli](https://github.com/Frikanalen/fk-cli). Neither of them does any of the work:

```bash
fk archive refresh-metadata     # videos whose duration, frame rate or loudness is missing
fk archive backfill             # videos whose formats are missing or built by an old profile
```

Both print what they found and change nothing. `--apply` puts those videos in the queue, at priority 0 so a member's upload is always claimed ahead of them.

**They need an API token and nothing else** — no SSH key, no archive, no ingest configuration. That is a property rather than an economy: a plan's *actions* fall out of the videofile rows alone, and only its notes — a row whose file is missing, media nothing claims — need the archive in front of them. So the decision of what to queue is a catalogue question, and the worker that claims a video reads the archive and decides again before it does anything. Nothing there can hand a worker a stale instruction.

They live in fk-cli rather than here because they talk to django-api and to nothing this repository has. An operator queueing work has a token and that binary; ingest is a Kubernetes deployment, and there is no checkout on the laptop for a script to be run from. What they do need from here is what a converged video looks like *at the deployed revision*, and that is published at [`/ingest-api/formats`](#publishing-the-format-revisions) rather than read out of a working tree — so a sweep can no longer plan against a template the pool has not got yet.

They look at the catalogue, and only the catalogue. Nothing there reads the archive root, because an archived directory the catalogue has dropped is not either command's subject — there is no job to queue for a video with no row, and that is [`fk-archive-gc`](archive-utils/README.md#garbage-collection)'s on the storage host.

`--apply` leaves alone any video ingest is working on right now, since overwriting that job would reset somebody's upload under them. Nothing it can queue takes media out of the published tree, so there is no confirmation to give.

Then scale the pool and let it drain. It is safe to close the terminal: the queue is the state, and a worker re-plans each video when it claims it.

```bash
kubectl scale deployment/ingest-workers --replicas=6
```

Re-running is how you resume. What each tool queues is derived from what the catalogue actually says, so anything already done is simply not queued again.

### What is not on the queue

Everything derived from a video's original goes through it, uploads included. Two things do not, both for structural reasons rather than as a transition.

**Programme images.** An ingest job is keyed on its video — one row, no history — so two images with different roles would be two pieces of work sharing one row, colliding with that video's own ingest state. An image is a ≤10 MB validation with no transcode; it gains nothing from a queue and stays in the hook.

**Reclaiming deleted videos.** An ingest job belongs to a video, so a video the catalogue has deleted has no job to enqueue and none to claim. It is not on the queue because it is not in this repository at all — see below.

`IngestKind` still has both values, but it no longer says where a job's source is — after the hook, every job's source is the archive. It says who is waiting: a member, or a reconciler. That distinction earns its keep twice. It lets a small pool serve uploads without ever queueing behind a catalogue-wide re-encode, and it gates the completion step — only an `upload` job sets `proper_import`, because only it promises the video ends importable. A backfill flipping that flag on a legacy video would publish something the catalogue is currently hiding.

### Reclaiming deleted videos

A video deleted from django-api leaves its directory in the archive behind. Collecting it is a comparison of two whole collections rather than work on a video — there is no job to queue and no worker to claim one — so it is [`fk-archive-gc`](archive-utils/README.md#garbage-collection), on the host holding the archive:

```bash
ssh file01 sudo fk-archive-gc prod            # says what it would reclaim
ssh file01 sudo fk-archive-gc prod --apply    # moves it into .trash/
```

It is guarded twice, because its subject is the entire archive: the catalogue read refuses to hand back a partial answer, and the sweep stops if more than `--max-delete-fraction` of the archive turns out to be unaccounted for.

## Deployment

`chart/` holds the Helm chart. It deploys two things: the `-upload` Deployment, which runs tusd alongside ingest in the same pod so tusd reaches the hook endpoint over the pod's loopback and the two share the upload volume rather than passing files across a network; and the `-workers` Deployment, which drains the ingest queue and is described under [Queue workers](#queue-workers). Two paths are exposed through an ingress on the upload hostname — tusd, and the read-only `/ingest-api/formats` — and the rest of ingest's endpoints stay cluster-internal.

Because one pod owns the upload volume, the upload Deployment runs a single replica with the `Recreate` strategy. Going multi-replica would need `ReadWriteMany` storage and session affinity, since a resumed upload has to reach the pod holding its partial file.

tusd is served at `/upload` on the main site — `https://frikanalen.no/upload`, `https://staging.frikanalen.no/upload` — sharing the host with the frontend at `/` and the API at `/api`, so no upload subdomain is needed per environment. The ingress path is tusd's own `basePath` and is passed through unstripped, so tusd sees the URLs it advertises.

`FK_UPLOAD_URL` in the Django settings must point at that same URL, since it is handed to the frontend as a video's `uploadUrl`.

### Publishing the format revisions

`GET /ingest-api/formats` answers what a converged video looks like *here, now*:

```json
{
  "image": "ghcr.io/frikanalen/ingest:v1.2.3",
  "formats": { "large_thumb": 1, "med_thumb": 1, "small_thumb": 1, "dash": 1 }
}
```

It exists because deciding which videos to queue happens outside this repository, and that decision needs one thing only this repository holds: which revision of each format the deployed image produces. `DESIRED_FORMATS` and the revision each template carries are declared in `app/formats.py`; a second copy of them in the tool that queues work is the half that would rot, since bumping a revision here would silently need a matching edit there. So it is published rather than copied, and read live rather than stored — if the pod is not answering, a caller gets a connection error instead of a plausible answer from whenever it last spoke.

It is unauthenticated, because reading it grants nothing: queueing work still goes through django-api under the operator's own token, so the whole exposure is that a stranger may learn which revision of DASH we are on.

The ingress rule for it is `pathType: Exact`, deliberately. It is the only rule pointing at ingest's own HTTP port, and a prefix rule there would put the rest of the application — `/tusdHooks` included, which is what starts an ingest — on the public internet. The path is repeated in `chart/templates/ingress.yaml` and in the router's prefix in `app/main.py`, and nothing checks that the two agree.

`image` is reported because the upload pod and the worker pool are separate Deployments off one image. Mid-rollout this endpoint can answer with a revision that part of the pool cannot build yet, and a job queued at that revision is claimed, re-planned against the older template, rebuilt at the older revision, and left stale with nothing to queue it again. Printing the image an operator planned against is what makes "run the sweep once the rollout has settled" checkable rather than folklore.

#### Upgrading past the rename

The upload half used to be named after the release alone. It is now suffixed `-upload` throughout — Deployment, Service, Ingress and claim — because it is the half that owns tusd and the upload volume rather than the whole of ingest, and a name that says so is worth more than the continuity of the old one.

To Kubernetes every one of those is a delete and a create. Two need care.

The Deployment owns a `ReadWriteOnce` volume, so its replacement cannot attach until the old pod has released it. Delete it yourself rather than trusting Helm to order the two.

**The claim is renamed, which means the old volume is abandoned with whatever is on it** — uploads still in flight, and anything ingest had not finished archiving. Helm will not remove it either way, since it carries `helm.sh/resource-policy: keep`, so it lingers until deleted by hand. Do this when nothing is uploading:

```bash
kubectl delete deployment/ingest --wait
helm upgrade ingest ./chart
kubectl delete pvc/ingest-uploads
```

The old Service and Ingress go with the upgrade; Helm removes them itself, since they are in the previous release and no longer rendered. Only the claim survives to be deleted deliberately, which is the point of the policy.

### Queue workers

`workers` in the chart is a second Deployment of the same image running `python -m app.worker`, which claims jobs from django-api's ingest queue instead of serving tusd's hooks. It mounts no upload volume, and that is the point: the upload volume is `ReadWriteOnce` and therefore single-node, which is what pins the ingest pod to one replica. A worker needs only the archive, an API token and scratch space, so the pool scales freely.

It has no Service and no ingress. Workers reach out to django-api and to the archive; nothing reaches in, so there is no endpoint to protect.

Capacity is `replicas`, and nothing else — a worker asks for a job only when it is free, so no dispatcher has to know how many there are. **It must not be zero**: member uploads are drained by this pool, and a job nobody claims sits at `pending` indefinitely with nothing to notice or complain. It ships at two, which covers a burst of a few simultaneous uploads; scale it up for a backfill:

```bash
kubectl scale deployment/ingest-workers --replicas=6
```

That is reverted by the next `helm upgrade`, which sets it back to `workers.replicaCount` — so set it there for anything you want to keep, and be aware that a routine image bump restores whatever that says. Overshooting is safe: the scheduler leaves the surplus Pending rather than overcommitting the nodes.

Scaling down to zero stops uploads being processed. It does not fail them — the jobs wait, and are claimed whenever a worker comes back — but nothing tells the member, and `pending` is indistinguishable from a video nobody has uploaded to yet.

`workers.kind` says which queue the pool serves. Every job's source is the archive — the hook archives the original before it queues anything — so this is about who is waiting rather than what a worker can reach. Empty serves both, which is the normal deployment; setting it to `upload` on a second, small pool is how you keep a member's upload out of a lane busy with a catalogue-wide re-encode.

Scaling down mid-encode costs the encode. `SIGTERM` makes a worker stop claiming and finish the job it holds, but only within `terminationGracePeriodSeconds`; anything still running when that elapses is killed, and its lease expires so another worker picks the video up later. The default is an hour, which is also how long a node drain will wait for a worker.

That hour is a ceiling, not a wait. A worker sleeping on an empty queue is woken by the signal rather than sitting out the rest of its poll interval, so it exits within milliseconds and the kubelet moves on; the grace period is only spent when there is really an encode to see out. A worker that is signalled while its claim is still in flight hands that job straight back rather than starting hours of work it has no time to finish: it reports the state to `pending`, which is claimable, so the next worker to ask takes the video rather than the queue stalling on it until the lease expires. If that handover cannot be delivered the lease still recovers the job, so it is best-effort and never delays the exit.

Rollouts therefore retire a worker before starting its replacement (`maxSurge: 0`, `maxUnavailable: 1`) rather than the other way around. At two replicas the default rounds to the opposite, which deadlocks: nothing may be terminated until the replacement is Ready, the replacement cannot schedule until the outgoing worker's CPU request is freed, and the outgoing worker is never signalled, so it never frees it. `progressDeadlineSeconds` is set above the grace period for the same reason — otherwise a rollout that correctly waits out an encode is reported as a failed one.

`workers.resources.requests.cpu` is sized for a pool that is idle most of the time and busy in bursts, not for what a ladder can use at peak: a request is reserved around the clock, so a large one takes `replicaCount ×` that out of the cluster permanently. Nothing is lost by asking for less, because there is deliberately no `limits.cpu` — a burst still bursts into every idle core on the node, and the request only decides what is guaranteed when the node is contended. If bursts queue rather than merely running slowly, add workers rather than enlarging each one's reservation.

The SSH credentials come from a Kubernetes secret created outside the chart, by the `ingest_archive_account` role in the [infra](https://github.com/Frikanalen/infra) repository. The private key is generated on first run and stored only in that secret, so it never passes through Git or the vault. That role's README covers rotation, and the `authorized_keys` line that pins the key to `fk-archive-ssh <profile>` as a forced command — which is what supplies the profile ingest never sends, and what leaves the key with `fk-archive` and nothing else.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Go (for integration tests with tusd)

## Installation

Install dependencies using uv:

```bash
uv sync
```

## Running the server locally

Start the local Django API used by ingest:

```bash
docker compose up -d
```

Generate the Django API client as described in [Code generation](#code-generation), then create ingest's working directories:

```bash
mkdir -p upload archive
```

Create a `.env` file with settings for the local Django API. Port `8081` avoids clashing with the API exposed by Docker Compose on port `8000`:

```dotenv
FK_API_URL=http://localhost:8000
FK_API_USERNAME=test@superuser.lol
FK_API_PASSWORD=superuser
FK_TUSD_DIR=./upload
FK_ARCHIVE_DIR=./archive
FK_PORT=8081
```

This archives into `./archive` locally; no SSH setup is needed for development. Add `FK_DEBUG=true` if you want the `/watchFolder` endpoints — see [Debug mode](#debug-mode).

Start ingest with:

```bash
uv run python -m app.main
```

The health endpoint is available at <http://localhost:8081/internal/isAlive>.

## Code generation

### Django API client

The repository keeps an OpenAPI snapshot in `schema.yaml`. Two scripts manage schema updates and client generation:

**Fetch the latest schema from the backend:**

```bash
./scripts/update-schema.sh
```

This fetches the current schema from `http://localhost:8000/api/schema` and overwrites `schema.yaml`. The backend must be running locally.

**Regenerate the Python client from the schema:**

```bash
./scripts/generate-client.sh
```

This runs `openapi-python-client` to generate the Python client code from `schema.yaml` into `frikanalen_django_api_client/`.

For local development, run both scripts in sequence. CI runs `scripts/generate-client.sh` before building the Docker image, so the generated client is included despite being ignored by Git.

### tusd hook types

The tusd hook request schema is also generated into Pydantic types:

```shell
scripts/generate-hook-types.sh
```

## Testing

```shell
uv run python -m pytest
```

The deployed archive is tested against an in-process server shaped like the
storage host: the real `fk_archive_utils` behind an exec request, with the
archive itself a directory both ends can see, as the mount makes it.
`archive-utils/src` is on the test path for that reason and no other —
it is not a dependency of this package and must not become one. The two ship
separately and agree only on a command line, an exit code and a line of JSON, so
a stand-in for `fk-archive` would be free to keep agreeing with an engine that
had drifted away from it.

`archive-utils/` has its own suite, which a run from here does not collect:

```shell
cd archive-utils && uv run pytest
```
