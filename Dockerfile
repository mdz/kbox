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

# Install the project itself (no cache - avoid shebang issues from host builds)
RUN uv sync --locked

# Fix: uv sync recreates venv without system-site-packages; restore it
RUN sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' /app/.venv/pyvenv.cfg

# Place venv executables at front of path
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["python3", "-m", "kbox.main"]
