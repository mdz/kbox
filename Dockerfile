FROM debian:stable

RUN apt update && \
    apt -y install python3-gst-1.0 gstreamer1.0-alsa python3-mido python3-rtmidi rubberband-ladspa gstreamer1.0-plugins-bad && \
    apt clean

WORKDIR /srv/kbox
COPY . .
CMD ["python3","-m","kbox.main"]
