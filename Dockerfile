FROM debian:stable

RUN apt update && \
    apt -y install python3-gst-1.0 gstreamer1.0-alsa python3-mido python3-rtmidi \
    rubberband-ladspa gstreamer1.0-plugins-bad gstreamer1.0-plugins-good \
    python3-pip python3-venv ffmpeg && \
    apt clean

WORKDIR /srv/kbox

# Copy dependency files first for better caching
COPY requirements.txt .

# Install Python dependencies (this layer will be cached unless requirements.txt changes)
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

EXPOSE 8000
CMD ["python3", "-m", "kbox.main"]
