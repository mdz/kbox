import sys

class Config:
    if sys.platform == 'darwin':
        GSTREAMER_SOURCE = 'osxaudiosrc'
        GSTREAMER_SINK = 'osxaudiosink'

        midi_input = 'MPK mini 3'
        audio_input = None
        audio_output = None
    elif sys.platform == 'linux':
        GSTREAMER_SOURCE = 'alsasrc'
        GSTREAMER_SINK = 'alsasink'

        midi_input = 'MPK mini 3'
        audio_input = 'plughw:CARD=CODEC,DEV=0'
        audio_output = audio_input

    enable_audio = True
    enable_midi = True
