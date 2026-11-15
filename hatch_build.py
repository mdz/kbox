"""Hatch build hook: build the native signalsmithpitch GStreamer plugin.

The plugin (native/gst-signalsmith-pitch/) is a compiled .so that is not
checked in, so every fresh checkout -- and every new git worktree -- starts
without it. Without it, pitch shifting silently falls back to a passthrough
element and the test_signalsmith_* tests fail.

`uv sync` (which `uv run` does implicitly) builds kbox to install it into the
venv, so building the plugin here makes it part of setting up a checkout,
with no separate manual step. pyproject.toml lists the .so in
`tool.uv.cache-keys`, so uv re-runs this hook when the .so is missing even in
a checkout whose venv already exists.

Builds only when the .so is missing. The Docker image builds the plugin in
its own stage and copies it in before `uv sync`, so this is a no-op there;
anyone changing the plugin's sources reruns build.sh themselves.

Never fails the install: the plugin is optional at runtime, and CI's lint and
e2e jobs run `uv sync` on machines without GStreamer dev packages. A failed
build is reported on stderr (visible with `uv sync -v`); the
test_signalsmith_* tests are what fail loudly when the plugin is missing.
"""

import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

PLUGIN_DIR = Path("native") / "gst-signalsmith-pitch"
PLUGIN_SO = PLUGIN_DIR / "build" / "libgstsignalsmithpitch.so"


class SignalsmithPitchBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        root = Path(self.root)
        build_script = root / PLUGIN_DIR / "build.sh"
        if (root / PLUGIN_SO).exists() or not build_script.exists():
            return

        result = subprocess.run([str(build_script)], capture_output=True, text=True)
        if result.returncode != 0:
            print(
                f"warning: building the signalsmithpitch plugin failed "
                f"({build_script} exited {result.returncode}); pitch shifting "
                f"will fall back to passthrough.\n{result.stdout}{result.stderr}",
                file=sys.stderr,
            )
