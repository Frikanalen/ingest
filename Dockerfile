FROM python:3.12 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6.9 /uv /uvx /bin/
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev
ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Then, use a final image without uv
FROM python:3.12

# ffmpeg, not from apt. Debian trixie ships 7.1, which is two releases behind,
# and the archive is transcoded once and kept forever -- it is worth doing that
# with the current encoders. These are statically linked binaries built with
# libx264, libvpx and the dash muxer, so they bring no shared libraries into
# the image and nothing here depends on Debian's ffmpeg packaging. Pinned: an
# ffmpeg change is an encoder change, and those want to be deliberate.
COPY --from=mwader/static-ffmpeg:9.0.1 /ffmpeg /ffprobe /usr/local/bin/

# It is important to use the image that matches the builder, as the path to the
# Python executable must be the same, e.g., using `python:3.11-slim-bookworm`
# will fail.

WORKDIR /app

# A real account rather than a bare `USER 1000:4203`. Archiving happens over
# SSH, and asyncssh looks up the local username before it will open a
# connection, so a uid with no passwd entry fails every upload with "Unknown
# local username". Having a home directory also gives it somewhere to expand
# ~ to. 4203 matches the group that owns the media archive on file01, which
# now only matters for the upload volume shared with tusd.
RUN groupadd --gid 4203 fkupload \
    && useradd --uid 1000 --gid 4203 --create-home --shell /usr/sbin/nologin ingest

# Copy the application from the builder
COPY --from=builder --chown=ingest:fkupload /app .

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

USER ingest:fkupload

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
