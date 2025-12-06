import sys

class Config:
    if sys.platform == 'darwin':
        GSTREAMER_SOURCE = 'osxaudiosrc'
        # Use autoaudiosink which will auto-detect the best audio sink
        GSTREAMER_SINK = 'autoaudiosink'
        RUBBERBAND_PLUGIN = 'ladspa-ladspa-rubberband-dylib-rubberband-r3-pitchshifter-stereo'

        audio_input = None
        audio_output = None
    elif sys.platform == 'linux':
        GSTREAMER_SOURCE = 'alsasrc'
        GSTREAMER_SINK = 'alsasink'
        RUBBERBAND_PLUGIN = 'ladspa-ladspa-rubberband-so-rubberband-r3-pitchshifter-stereo'

        audio_input = 'plughw:CARD=CODEC,DEV=0'
        audio_output = audio_input

    midi_input = 'MPK mini Play mk3'
    enable_midi = True
