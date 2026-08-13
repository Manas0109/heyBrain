# heyBrain container image
#
# This image is for the TEXT-based CLI flows only:
#   brain think "some text", brain recall, brain remember, brain resume,
#   brain list, brain show, brain reminders list/tick (storage only —
#   see the note on osascript below), and the test suite.
#
# Two things in plan.md are macOS-specific and DO NOT work in this container,
# by design, with no passthrough hack attempted here:
#   1. Live microphone capture (`sounddevice`, `brain think` voice mode) —
#      containers have no real audio device passthrough in the general case.
#   2. Reminder delivery via `launchd` + `osascript` notifications (issue #13)
#      — both are macOS-only APIs. `brain reminders tick` will still update
#      reminder rows in SQLite, but the actual notification banner cannot
#      fire from inside Linux.
# See the "Running in Docker" section in README.md for details.

FROM python:3.12-slim

# sounddevice binds to PortAudio via cffi at import time (no compilation
# needed), but it still needs the shared library present.
RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY tests/ ./tests/

# Install the project (non-editable) with dev extras so `pytest` runs too.
RUN pip install --no-cache-dir ".[dev]"

# faster-whisper's base.en model (~150MB) is intentionally NOT baked into
# this image. It downloads once on first transcription/`brain doctor` run
# and is cached under $HEYBRAIN_HOME/models, which is a mounted volume —
# so the download happens at most once per host, not per container run.
# Tradeoff: smaller image (this dep is unused in the container anyway,
# since there's no mic), at the cost of a one-time delay if voice/doctor
# model-warming is ever exercised inside the container. For a hackathon-
# scoped, text-first container image, lean beats pre-warmed.

# SQLite (brain.db) and Chroma (chroma/) both persist under $HEYBRAIN_HOME.
# This must stay a mounted volume — data is never baked into the image.
ENV HEYBRAIN_HOME=/root/.heybrain
VOLUME ["/root/.heybrain"]

# AWS credentials are never baked into the image. At runtime, provide them
# via -e AWS_REGION/AWS_PROFILE/AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or by
# mounting ~/.aws read-only, matching .env.example. See README.md.

ENTRYPOINT ["brain"]
CMD ["--help"]
