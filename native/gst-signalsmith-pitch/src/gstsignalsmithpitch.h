#ifndef __GST_SIGNALSMITH_PITCH_H__
#define __GST_SIGNALSMITH_PITCH_H__

#include <gst/audio/gstaudiofilter.h>

G_BEGIN_DECLS

#define GST_TYPE_SIGNALSMITH_PITCH (gst_signalsmith_pitch_get_type())
G_DECLARE_FINAL_TYPE(GstSignalsmithPitch, gst_signalsmith_pitch, GST, SIGNALSMITH_PITCH, GstAudioFilter)

G_END_DECLS

#endif
