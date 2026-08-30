# Build the native signalsmithpitch GStreamer plugin in its own stage, so the
# GStreamer dev headers/compiler it needs don't end up in the final image.
FROM debian:stable-slim AS gst-signalsmith-pitch-build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        g++ \
        pkg-config \
        libgstreamer1.0-dev \
        libgstreamer-plugins-base1.0-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY native/gst-signalsmith-pitch /build/native/gst-signalsmith-pitch
RUN /build/native/gst-signalsmith-pitch/build.sh

# Use Debian base for GStreamer system packages
FROM debian:stable-slim

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system packages including GStreamer and Python bindings
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-gi \
        python3-gst-1.0 \
        gstreamer1.0-alsa \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-x \
        gstreamer1.0-tools \
        alsa-utils \
        rubberband-ladspa \
        ffmpeg \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create venv with access to system site-packages (for gi module)
RUN uv venv --system-site-packages

# Install dependencies using the lockfile (include dev deps for testing in container)
# Note: no uv cache mount to avoid shebang path issues from host builds
RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Copy the project into the image
COPY . /app

# Bring in the pre-built native signalsmithpitch plugin (see build stage above)
COPY --from=gst-signalsmith-pitch-build \
    /build/native/gst-signalsmith-pitch/build/libgstsignalsmithpitch.so \
    /app/native/gst-signalsmith-pitch/build/libgstsignalsmithpitch.so

# Install the project itself (no cache - avoid shebang issues from host builds)
RUN uv sync --locked

# Fix: uv sync recreates venv without system-site-packages; restore it
RUN sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' /app/.venv/pyvenv.cfg

# Place venv executables at front of path
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["python3", "-m", "kbox.main"]
