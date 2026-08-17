# Ingest

This used to go by move-and-process and fkprocess.

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

Generate the Django API client as described in [Code generation](#code-generation), then create the directories ingest watches:

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
