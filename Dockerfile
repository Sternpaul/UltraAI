# UltraAI processing worker (the heavy pipeline that runs on a Linux server).
#
# The Studio app bundles its own ffmpeg; the worker needs ffmpeg on the server for
# audio decode/encode, so it is installed here.
#
#   docker build -t ultraai-worker .
#   docker run --rm -v "$HOME/.cache/ultraai:/cache" ultraai-worker process JOBDIR
#
# The image sets ULTRAAI_CACHE=/cache and declares /cache a volume, so model downloads
# and per-song step caches survive container restarts when the host dir is mounted.

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# keep pip itself patched: the CI in-image pip-audit flags vulnerable pips, and
# the python:3.12-slim base image ships whatever pip was current at its build
RUN pip install --no-cache-dir --upgrade "pip>=26.1.2"

COPY pyproject.toml ./
COPY ultraai ./ultraai
# a C compiler only while installing: some worker deps (diffq) build from source
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir ".[worker]" \
    && apt-get purge -y build-essential && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

ENV ULTRAAI_CACHE=/cache
VOLUME /cache

ENTRYPOINT ["ultraai"]
CMD ["--help"]
