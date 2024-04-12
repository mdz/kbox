import sys

class Config:
    if sys.platform == 'darwin':
        midi_input = 'MPK mini 3' # 'MPK mini 3:MPK mini 3 MIDI 1 28:0'
        audio_input = ('osxaudiosrc',None)
        audio_output = ('osxaudiosink',None)
    elif sys.platform == 'linux':
        #midi_input = 'MPK mini 3:MPK mini 3 MIDI 1 28:0'
        midi_input = 'MPK mini 3'
        #audio_input = ('alsasrc', 'plughw:CARD=CODEC,DEV=0')
        #audio_output = ('alsasink', 'plughw:CARD=CODEC,DEV=0')
        audio_input = ('alsasrc', 'plughw:0,0')
        audio_output = ('alsasink', 'plughw:0,0')
    enable_audio = True
    enable_midi = True
