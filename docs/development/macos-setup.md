# macOS Setup Guide

macOS is kbox's development platform, not a deployment target. The goal of
this page is a checkout where `uv run pytest` passes with no failures and
`uv run python -m kbox.main` plays audio.

## Prerequisites

1. **Homebrew**, if you don't have it:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **GStreamer and GObject introspection**, from Homebrew:

   ```bash
   brew install gstreamer glib gobject-introspection pkgconf
   ```

   Homebrew's GStreamer is required, not optional: the copy of GStreamer that
   comes with macOS is too old and is missing plugins kbox's pipeline needs.

3. **Python dependencies**:

   ```bash
   uv sync --group dev
   ```

   PyGObject and pycairo are declared in `pyproject.toml` under
   `sys_platform == 'darwin'`, so `uv sync` installs them on a Mac and skips
   them elsewhere. Don't `uv pip install pygobject` by hand.

4. **The native `signalsmithpitch` plugin**. The Docker image builds this in
   its own stage; a native checkout does not, so you build it once:

   ```bash
   native/gst-signalsmith-pitch/build.sh
   ```

   Without it, pitch shifting silently falls back to a passthrough element
   **and the four `test_signalsmith_*` tests in `test/test_streaming.py`
   fail** — by design, so missing coverage shows up red instead of vanishing.
   If you see those four failing in an otherwise green suite, this step is
   what you skipped. The build output is a `.so`, which `.gitignore` already
   covers.

## Environment variables

You do not need to export `DYLD_LIBRARY_PATH`, `GI_TYPELIB_PATH` or
`GST_PLUGIN_*` by hand. `configure_macos_gstreamer_env()` in
[`kbox/platform.py`](../../kbox/platform.py) sets them, and it is called from
`kbox.main` for the app and from `test/conftest.py` for the test suite. It
has to run before anything in the process imports `gi`; conftest.py is
imported by pytest before any test module, so every invocation is covered —
the whole suite, one file, or a single test.

Read that function's docstring before changing any of this. In particular, it
sets `GST_PLUGIN_SYSTEM_PATH_1_0` rather than prepending to it, deliberately.

## Two GStreamers on one Mac

A Mac can end up with both Homebrew's GStreamer and the official
`GStreamer.framework` binary installer under `/Library/Frameworks/`. Having
both is fine and does not need cleaning up — `configure_macos_gstreamer_env()`
exists precisely to pin the process to Homebrew's copy so libraries from one
install can't be paired with plugins from the other.

What that mismatch looks like when it does happen (i.e. when something
imported `gi` before the env was configured):

```
GLib-GIRepository-WARNING: Failed to load shared library 'libgobject-2.0.0.dylib'
...
gi/overrides/__init__.py:209: AssertionError
```

Dozens of failures across `test_streaming.py`, `test_config_manager.py` and
`test_api.py` with that signature mean the env setup didn't run, not that the
host install is broken. Check that `test/conftest.py` is still in place and
still calls `configure_macos_gstreamer_env()`, and that nothing imports `gi`
at module scope ahead of it. Homebrew's GStreamer being a different version
from the framework's is expected and harmless under that pinning.

To confirm which GStreamer a process actually got:

```bash
uv run python -c "
import kbox.platform as p; p.configure_macos_gstreamer_env()
import gi; gi.require_version('Gst', '1.0')
from gi.repository import Gst; Gst.init(None)
print(gi.__file__); print(Gst.version_string())
"
```

`gi` should resolve inside `.venv`, and the version string should match
`brew list --versions gstreamer`.

Harmless noise you can ignore from that command and from the app: `objc[...]
Class ... is implemented in both ...libgtk-3...` duplicate-class warnings
(two GTK major versions in the Homebrew prefix), and the
`gst_audio_ring_buffer_set_channel_positions` GStreamer-Audio-CRITICAL
described in `load_file()` in `kbox/streaming/controller.py`.

## Running

```bash
uv run python -m kbox.main      # http://localhost:8000
uv run pytest                   # should be fully green once step 4 is done
```

## Pre-push hook

`.pre-commit-config.yaml` has a `pytest` hook at `stages: [pre-push]`, and
sets `default_install_hook_types: [pre-commit, pre-push]`, so both hook
types get wired by:

```bash
pre-commit install
```

That hook was previously left uninstalled because a full local `uv run
pytest` failed on this platform. Once the setup above is done it passes, so
installing it no longer blocks pushes.
