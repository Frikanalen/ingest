# Ingest

Ingest handles tusd uploads for Frikanalen by validating metadata, archiving source files, generating derivative media, and updating the Django API.

It exposes these application endpoints:

- `POST /tusdHooks/` handles tusd's `pre-create` and `post-finish` hooks. `pre-create` validates the upload metadata and assigns its storage path; `post-finish` starts the ingest job.
- `GET /internal/isAlive` is the health check.
- `GET /watchFolder/tusFiles` and `GET /watchFolder/archive` stream directory listings as server-sent events for debugging. Filesystem changes do not start ingest jobs.

For a completed upload, ingest checks the media with FFprobe and copies the source file from `FK_TUSD_DIR` to `<archive>/<video-id>/original/<filename>`. FFmpeg outputs are stored alongside it by format, currently as `<archive>/<video-id>/large_thumb/<stem>.jpg`, `<archive>/<video-id>/med_thumb/<stem>.jpg`, `<archive>/<video-id>/small_thumb/<stem>.jpg`, `<archive>/<video-id>/webm_med/<stem>.webm` and `<archive>/<video-id>/dash/`. Ingest records the original and generated files, duration, upload time, and completion status in the Django API. The uploaded file is removed from `FK_TUSD_DIR` once the whole job has succeeded, so a failed ingest leaves it in place.

FFmpeg always reads the uploaded file where tusd left it and writes to local scratch space; only finished files are handed to the archive. That is what lets the archive live on another host.

## Formats

Each format in `templates/` is a command with a YAML header, and each gets a scratch directory of its own. Whatever the command leaves in that directory is archived; anything that must not be archived, like a two-pass log, goes in `scratch_dir` instead. The header names the format's primary output — the one file registered with the Django API, and the last one published — either as an extension applied to the source file's stem (`output_file_extension`) or as a fixed name (`output_file_name`).

Publishing order matters because the archive is exported read-only to the playout hosts: the primary output goes last, so a manifest is never readable before the media it references has arrived.

### Thumbnails

`large_thumb` (720px wide), `med_thumb` (320px) and `small_thumb` (120px) are single frames pulled a quarter of the way into the video, each a single-pass FFmpeg invocation. Three sizes exist so a consumer — a detail page, a grid of cards, a compact list row — can pick the one closest to what it actually renders, rather than every context fetching the 720px original and scaling it down in the browser.

### DASH

`dash` is an adaptive VP9/Opus ladder — 1080p, 720p and 360p, none of them upscaled past the source — played back over MSE by a browser-side player. It is one FFmpeg invocation: the source is decoded once, padded to 16:9, and split into the three renditions, which costs less wall time and less CPU than the single two-pass `webm_med` encode beside it.

Segments live inside one file per representation, addressed by byte range from the manifest (`-single_file 1`), rather than as a file each. A one-hour video is five files instead of several thousand, which is what makes DASH viable over an archive reached by SFTP. The cost is a manifest that grows with duration, by roughly 120 KB per hour; it compresses well, and if it ever becomes a problem the fix is rewriting the manifest into on-demand `SegmentBase` form.

Keyframes are pinned to wall-clock time rather than a GOP length in frames, so renditions stay aligned for switching whatever frame rate is uploaded. A source with no audio track gets no audio adaptation set, since an adaptation set with no representation in it is not valid DASH.

Serving it needs HTTP range requests, CORS, and `application/dash+xml` on the `.mpd`; none of that is ingest's side of the job.

## Archive

The archive is either a local directory or a directory on another host reached over SSH. Setting `FK_ARCHIVE_HOST` selects the latter, and `FK_ARCHIVE_DIR` then refers to a path on that host.

| Setting | Meaning |
| --- | --- |
| `FK_ARCHIVE_DIR` | Where finished files go. A local path, or a path on `FK_ARCHIVE_HOST`. |
| `FK_ARCHIVE_HOST` | Archive host. Unset means archive locally. |
| `FK_ARCHIVE_PORT` | SSH port, default `22`. |
| `FK_ARCHIVE_USERNAME` | SSH user, default `ingest`. |
| `FK_ARCHIVE_PRIVATE_KEY_FILE` | SSH private key to authenticate with. |
| `FK_ARCHIVE_KNOWN_HOSTS_FILE` | `known_hosts` file used to verify the archive host. |
| `FK_ARCHIVE_FALLBACK_DIR` | Local directory used when the SSH credentials are missing, default `./archive`. |
| `FK_ARCHIVE_REQUIRED` | Fail startup instead of falling back. Set this in deployments. |
| `FK_WORK_DIR` | Local scratch space for transcoding. Defaults to the system temporary directory. |

Files are transferred under a `.part` name and renamed into place once complete, so an interrupted transfer cannot leave a truncated file that later looks like a finished one.

Both SSH credentials must be given explicitly — ingest will not reach for the running user's `~/.ssh`, and it never disables host key verification. If either is missing, ingest logs a warning and archives to `FK_ARCHIVE_FALLBACK_DIR` instead, so you can run it locally without setting up SSH at all. **Set `FK_ARCHIVE_REQUIRED=true` anywhere that actually archives over SSH**: otherwise a secret that fails to mount leaves ingest quietly writing to scratch space, where files are lost on restart.

## Deployment

`chart/` holds the Helm chart. It deploys tusd alongside ingest in the same pod, so tusd reaches the hook endpoint over the pod's loopback and the two share the upload volume rather than passing files across a network. Only tusd is exposed, through an ingress on the upload hostname; ingest's own endpoints stay cluster-internal.

Because one pod owns the upload volume, the chart runs a single replica with the `Recreate` strategy. Going multi-replica would need `ReadWriteMany` storage and session affinity, since a resumed upload has to reach the pod holding its partial file.

tusd is served at `/upload` on the main site — `https://frikanalen.no/upload`, `https://staging.frikanalen.no/upload` — sharing the host with the frontend at `/` and the API at `/api`, so no upload subdomain is needed per environment. The ingress path is tusd's own `basePath` and is passed through unstripped, so tusd sees the URLs it advertises.

`FK_UPLOAD_URL` in the Django settings must point at that same URL, since it is handed to the frontend as a video's `uploadUrl`.

The SSH credentials come from a Kubernetes secret created outside the chart, by the `ingest_archive_account` role in the [infra](https://github.com/Frikanalen/infra) repository. The private key is generated on first run and stored only in that secret, so it never passes through Git or the vault. That role's README covers the `authorized_keys` restrictions on the archive host and rotation.

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

This archives into `./archive` locally; no SSH setup is needed for development.

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
