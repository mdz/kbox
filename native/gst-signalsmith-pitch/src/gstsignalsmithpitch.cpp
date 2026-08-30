/* GStreamer real-time pitch-shift element backed by signalsmith-stretch
 * (https://github.com/Signalsmith-Audio/signalsmith-stretch, MIT licensed).
 *
 * Correctly resets its internal state on FLUSH_STOP/EOS (see
 * gst_signalsmith_pitch_sink_event below), unlike gst-plugins-bad's LADSPA
 * wrapper, which leaks buffered audio across track transitions -- this
 * element exists specifically so kbox doesn't need to destroy/recreate a
 * LADSPA rubberband element per song to work around that.
 */

#include "gstsignalsmithpitch.h"

#include <gst/audio/audio.h>

#include <cstring>
#include <vector>

#include "signalsmith-stretch/signalsmith-stretch.h"

GST_DEBUG_CATEGORY_STATIC(gst_signalsmith_pitch_debug);
#define GST_CAT_DEFAULT gst_signalsmith_pitch_debug

enum { PROP_0, PROP_SEMITONES };

#define DEFAULT_SEMITONES 0.0
#define MIN_SEMITONES -24.0
#define MAX_SEMITONES 24.0

struct _GstSignalsmithPitch {
  GstAudioFilter parent;

  GMutex lock;
  gdouble semitones;

  gint channels;
  gint rate;
  signalsmith::stretch::SignalsmithStretch<float> *stretch;

  std::vector<float> in_planar;
  std::vector<float> out_planar;
  std::vector<const float *> in_ptrs;
  std::vector<float *> out_ptrs;
};

G_DEFINE_TYPE(GstSignalsmithPitch, gst_signalsmith_pitch, GST_TYPE_AUDIO_FILTER);

static void gst_signalsmith_pitch_set_property(GObject *object, guint prop_id,
                                                const GValue *value, GParamSpec *pspec);
static void gst_signalsmith_pitch_get_property(GObject *object, guint prop_id, GValue *value,
                                                GParamSpec *pspec);
static void gst_signalsmith_pitch_finalize(GObject *object);

static gboolean gst_signalsmith_pitch_setup(GstAudioFilter *filter, const GstAudioInfo *info);
static GstFlowReturn gst_signalsmith_pitch_transform(GstBaseTransform *base, GstBuffer *inbuf,
                                                      GstBuffer *outbuf);
static gboolean gst_signalsmith_pitch_sink_event(GstBaseTransform *base, GstEvent *event);
static gboolean gst_signalsmith_pitch_stop(GstBaseTransform *base);

static void destroy_stretch_locked(GstSignalsmithPitch *self) {
  delete self->stretch;
  self->stretch = nullptr;
}

static void gst_signalsmith_pitch_class_init(GstSignalsmithPitchClass *klass) {
  GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
  GstElementClass *element_class = GST_ELEMENT_CLASS(klass);
  GstBaseTransformClass *base_transform_class = GST_BASE_TRANSFORM_CLASS(klass);
  GstAudioFilterClass *audio_filter_class = GST_AUDIO_FILTER_CLASS(klass);

  gobject_class->set_property = gst_signalsmith_pitch_set_property;
  gobject_class->get_property = gst_signalsmith_pitch_get_property;
  gobject_class->finalize = gst_signalsmith_pitch_finalize;

  g_object_class_install_property(
      gobject_class, PROP_SEMITONES,
      g_param_spec_double("semitones", "Semitones",
                           "Pitch shift amount in semitones", MIN_SEMITONES, MAX_SEMITONES,
                           DEFAULT_SEMITONES,
                           (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  gst_element_class_set_static_metadata(
      element_class, "Signalsmith real-time pitch shifter", "Filter/Effect/Audio",
      "Real-time stereo-capable pitch shifting using signalsmith-stretch",
      "kbox project <https://github.com/mdz/kbox>");

  GstCaps *caps = gst_caps_from_string(
      "audio/x-raw, "
      "format = (string) " GST_AUDIO_NE(F32) ", "
      "layout = (string) interleaved, "
      "rate = (int) [ 1, MAX ], "
      "channels = (int) [ 1, MAX ]");
  gst_audio_filter_class_add_pad_templates(audio_filter_class, caps);
  gst_caps_unref(caps);

  audio_filter_class->setup = GST_DEBUG_FUNCPTR(gst_signalsmith_pitch_setup);
  base_transform_class->transform = GST_DEBUG_FUNCPTR(gst_signalsmith_pitch_transform);
  base_transform_class->sink_event = GST_DEBUG_FUNCPTR(gst_signalsmith_pitch_sink_event);
  base_transform_class->stop = GST_DEBUG_FUNCPTR(gst_signalsmith_pitch_stop);

  GST_DEBUG_CATEGORY_INIT(gst_signalsmith_pitch_debug, "signalsmithpitch", 0,
                           "Signalsmith real-time pitch shifter");
}

static void gst_signalsmith_pitch_init(GstSignalsmithPitch *self) {
  g_mutex_init(&self->lock);
  self->semitones = DEFAULT_SEMITONES;
  self->channels = 0;
  self->rate = 0;
  self->stretch = nullptr;

  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), FALSE);
}

static void gst_signalsmith_pitch_finalize(GObject *object) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(object);

  g_mutex_lock(&self->lock);
  destroy_stretch_locked(self);
  g_mutex_unlock(&self->lock);
  g_mutex_clear(&self->lock);

  G_OBJECT_CLASS(gst_signalsmith_pitch_parent_class)->finalize(object);
}

static void gst_signalsmith_pitch_set_property(GObject *object, guint prop_id,
                                                const GValue *value, GParamSpec *pspec) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(object);

  switch (prop_id) {
    case PROP_SEMITONES:
      g_mutex_lock(&self->lock);
      self->semitones = g_value_get_double(value);
      if (self->stretch) {
        self->stretch->setTransposeSemitones((float)self->semitones);
      }
      g_mutex_unlock(&self->lock);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
      break;
  }
}

static void gst_signalsmith_pitch_get_property(GObject *object, guint prop_id, GValue *value,
                                                GParamSpec *pspec) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(object);

  switch (prop_id) {
    case PROP_SEMITONES:
      g_mutex_lock(&self->lock);
      g_value_set_double(value, self->semitones);
      g_mutex_unlock(&self->lock);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
      break;
  }
}

static gboolean gst_signalsmith_pitch_setup(GstAudioFilter *filter, const GstAudioInfo *info) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(filter);

  g_mutex_lock(&self->lock);
  destroy_stretch_locked(self);

  self->channels = GST_AUDIO_INFO_CHANNELS(info);
  self->rate = GST_AUDIO_INFO_RATE(info);

  self->stretch = new signalsmith::stretch::SignalsmithStretch<float>();
  self->stretch->presetDefault(self->channels, (float)self->rate);
  self->stretch->setTransposeSemitones((float)self->semitones);

  self->in_ptrs.assign(self->channels, nullptr);
  self->out_ptrs.assign(self->channels, nullptr);

  GST_INFO_OBJECT(self, "configured for %d channels at %d Hz", self->channels, self->rate);
  g_mutex_unlock(&self->lock);
  return TRUE;
}

static GstFlowReturn gst_signalsmith_pitch_transform(GstBaseTransform *base, GstBuffer *inbuf,
                                                       GstBuffer *outbuf) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(base);
  GstMapInfo in_map, out_map;
  GstFlowReturn ret = GST_FLOW_OK;

  if (!gst_buffer_map(inbuf, &in_map, GST_MAP_READ)) {
    return GST_FLOW_ERROR;
  }
  if (!gst_buffer_map(outbuf, &out_map, GST_MAP_WRITE)) {
    gst_buffer_unmap(inbuf, &in_map);
    return GST_FLOW_ERROR;
  }

  g_mutex_lock(&self->lock);

  if (!self->stretch || self->channels <= 0) {
    GST_ELEMENT_ERROR(self, CORE, NOT_IMPLEMENTED, (NULL), ("not configured before transform"));
    ret = GST_FLOW_ERROR;
    goto out;
  }

  {
    const gsize bytes_per_frame = sizeof(float) * self->channels;
    const guint n = (guint)(in_map.size / bytes_per_frame);
    const float *in_interleaved = (const float *)in_map.data;
    float *out_interleaved = (float *)out_map.data;

    self->in_planar.resize((size_t)n * self->channels);
    self->out_planar.resize((size_t)n * self->channels);

    for (gint c = 0; c < self->channels; c++) {
      float *channel_buf = self->in_planar.data() + (size_t)c * n;
      for (guint i = 0; i < n; i++) {
        channel_buf[i] = in_interleaved[i * self->channels + c];
      }
      self->in_ptrs[c] = channel_buf;
      self->out_ptrs[c] = self->out_planar.data() + (size_t)c * n;
    }

    self->stretch->process(self->in_ptrs, (int)n, self->out_ptrs, (int)n);

    for (gint c = 0; c < self->channels; c++) {
      const float *channel_buf = self->out_ptrs[c];
      for (guint i = 0; i < n; i++) {
        out_interleaved[i * self->channels + c] = channel_buf[i];
      }
    }
  }

out:
  g_mutex_unlock(&self->lock);
  gst_buffer_unmap(inbuf, &in_map);
  gst_buffer_unmap(outbuf, &out_map);
  return ret;
}

static gboolean gst_signalsmith_pitch_sink_event(GstBaseTransform *base, GstEvent *event) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(base);

  switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_FLUSH_STOP:
    case GST_EVENT_EOS:
    case GST_EVENT_SEGMENT:
      g_mutex_lock(&self->lock);
      if (self->stretch) {
        GST_DEBUG_OBJECT(self, "resetting stretch state on %s", GST_EVENT_TYPE_NAME(event));
        self->stretch->reset();
      }
      g_mutex_unlock(&self->lock);
      break;
    default:
      break;
  }

  return GST_BASE_TRANSFORM_CLASS(gst_signalsmith_pitch_parent_class)->sink_event(base, event);
}

static gboolean gst_signalsmith_pitch_stop(GstBaseTransform *base) {
  GstSignalsmithPitch *self = GST_SIGNALSMITH_PITCH(base);

  g_mutex_lock(&self->lock);
  destroy_stretch_locked(self);
  g_mutex_unlock(&self->lock);

  return TRUE;
}

static gboolean plugin_init(GstPlugin *plugin) {
  return gst_element_register(plugin, "signalsmithpitch", GST_RANK_NONE,
                               GST_TYPE_SIGNALSMITH_PITCH);
}

#define VERSION "0.1.0"
#define PACKAGE "gst-signalsmith-pitch"

GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, signalsmithpitch,
                   "Real-time pitch shifting using signalsmith-stretch", plugin_init, VERSION,
                   "MIT", "gst-signalsmith-pitch", "https://github.com/mdz/kbox")
