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

3. **Python, from Homebrew — not uv's managed interpreter.** This is the
   step most likely to bite you, and it is worth getting right before
   anything else:

   ```bash
   brew install python@3.14
   uv venv --python $(brew --prefix python@3.14)/bin/python3.14
   uv sync --group dev
   ```

   PyGObject is a C extension that has to pair with Homebrew's glib. uv's
   managed interpreters are [python-build-standalone][pbs] builds, and they
   **cannot load Homebrew's PyGObject** — but uv prefers its own managed
   interpreter by default, so a plain `uv venv` may well pick one. Whether a
   checkout works then comes down to which interpreter uv happened to
   choose.

   [pbs]: https://github.com/astral-sh/python-build-standalone

   Measured on one machine, two worktrees of this repo, byte-identical
   PyGObject wheels (same sha256) and identical environments — the base
   interpreter was the only variable:

   | Venv base interpreter | `from gi.repository import Gst` |
   | --- | --- |
   | Homebrew `python@3.14` | works |
   | uv-managed standalone CPython 3.14.5 | `AssertionError` in `gi/overrides` |

   Confirmed in both directions by running each interpreter against the
   other's `site-packages`: the interpreter decides, the installed packages
   do not.

   To check an existing venv:

   ```bash
   grep home .venv/pyvenv.cfg
   ```

   A path under `~/.local/share/uv/python/` is the broken case — delete
   `.venv` and recreate it as above. A path under `/opt/homebrew` is fine.

   PyGObject and pycairo themselves are declared in `pyproject.toml` under
   `sys_platform == 'darwin'`, so `uv sync` handles them. Don't
   `uv pip install pygobject` by hand.

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
`test_api.py` with that signature mean the setup didn't take, not that the
host install is broken. In order of likelihood:

1. **The venv is on uv's managed interpreter** — see prerequisite 3 above.
   This is now caught: `configure_macos_gstreamer_env()` raises a
   `RuntimeError` naming the interpreter and the fix, rather than letting it
   surface as a wall of `assert g_type != TYPE_NONE`.
2. Something imported `gi` at module scope before
   `configure_macos_gstreamer_env()` ran. `test/conftest.py` covers the test
   suite; `kbox.main` covers the app.

Homebrew's GStreamer being a different version from the framework's is
expected and harmless under the pinning.

### Why `DYLD_LIBRARY_PATH` is not the fix

Worth knowing before you try to solve a path problem with a path variable:
typelibs reference their libraries by bare leaf name, and dyld reads the
`DYLD_*` variables **once, at process launch**. Setting
`DYLD_LIBRARY_PATH` from inside Python therefore has no effect on any later
`dlopen` — it is not that it is set too late in the function, it can never
work. `configure_macos_gstreamer_env()` still sets it, because child
processes like `gst-plugin-scanner` are launched fresh and do read it.

What pins the libraries in-process is loading them from Homebrew by
absolute path with `RTLD_GLOBAL` before `import gi`, so a later bare-name
`dlopen` resolves against the already-loaded image instead of searching.
Exporting `DYLD_LIBRARY_PATH` in your shell before launching also works,
and is a reasonable thing to try while debugging, but it is not a fix the
repo can ship.

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
