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

# ffmpeg, not from apt: Debian trixie ships 7.1, which is two releases behind,
# and the archive is transcoded once and kept forever -- it is worth doing that
# with the current encoders. Pinned: an ffmpeg change is an encoder change, and
# those want to be deliberate.
#
# Jellyfin's build, because one binary has to drive every encoder backend
# (see app.media.comand_template.Encoder): SVT-AV1 and x264 on CPU, QSV on
# Intel, NVENC on NVIDIA. It brings Intel's half of that with it -- libva, the
# iHD driver and the oneVPL runtime are under its own lib/, so nothing here
# depends on Debian's non-free section. NVIDIA's half is the driver's, mounted
# into the container by the NVIDIA runtime on a GPU node.
#
# Taken out of the Jellyfin server image rather than installed from Jellyfin's
# apt repository, the same way the static build this replaces was copied out of
# its image. Pinned by digest; the image is Jellyfin 12.1, carrying
# jellyfin-ffmpeg8 8.1.2-4-trixie.
#
# The previous static build could not do this at all: a static binary cannot
# load the VA driver, which is a shared object chosen at runtime.
COPY --from=jellyfin/jellyfin@sha256:78d3ea1207d1322471fcac39a614f004f2ccf7e878f95ab2977d752f07e4dd7e \
    /usr/lib/jellyfin-ffmpeg /usr/lib/jellyfin-ffmpeg

# What the jellyfin-ffmpeg8 package depends on that the base image does not
# already have. The ldd check fails the build, rather than the first encode,
# if a future base image drops one.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libbluray2 libmp3lame0 libmpg123-0t64 libnuma1 libogg0 libopenmpt0t64 libopus0 \
        libudfread0 libvorbis0a libvorbisenc2 libvorbisfile3 libvpx9 libx264-164 libx265-215 \
        libzvbi0t64 ocl-icd-libopencl1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
    && ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe \
    && ! ldd /usr/lib/jellyfin-ffmpeg/ffmpeg | grep "not found" \
    && ffmpeg -hide_banner -version | head -1

# Where libva finds the iHD driver Jellyfin's build carries, so QSV does not
# depend on one being installed in the image.
ENV LIBVA_DRIVERS_PATH=/usr/lib/jellyfin-ffmpeg/lib/dri

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
