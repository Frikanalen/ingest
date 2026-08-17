# Ingest

Ingest handles tusd uploads for Frikanalen by validating metadata, archiving source files, generating derivative media, and updating the Django API.

It exposes these application endpoints:

- `POST /tusdHooks/` handles tusd's `pre-create` and `post-finish` hooks. `pre-create` validates the upload metadata and assigns its storage path; `post-finish` starts the ingest job.
- `GET /internal/isAlive` is the health check.
- `GET /watchFolder/tusFiles` and `GET /watchFolder/archive` stream directory listings as server-sent events for debugging. Filesystem changes do not start ingest jobs.

For a completed upload, ingest checks the media with FFprobe and moves the source file from `FK_TUSD_DIR` to `FK_ARCHIVE_DIR/<video-id>/original/<filename>`. FFmpeg outputs are stored alongside it by format, currently as `FK_ARCHIVE_DIR/<video-id>/large_thumb/<stem>.jpg` and `FK_ARCHIVE_DIR/<video-id>/webm_med/<stem>.webm`. Ingest records the original and generated files, duration, upload time, and completion status in the Django API.

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
