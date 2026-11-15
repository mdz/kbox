"""
Suite-wide fixtures and environment setup.

The only job here today is pointing GStreamer/PyGObject at Homebrew's install
on macOS, before any test module gets imported.

Why it lives in a conftest rather than in the test modules that need it:
`configure_macos_gstreamer_env()` has to run before anything in the process
imports `gi`, and pytest imports conftest.py before it imports any test
module -- so this is the one place that holds for every invocation, whether
you run the whole suite, one file, or a single test. Doing it at the top of
individual test modules only works as long as the module that remembers to
call it happens to be collected before any module that reaches `gi`, which is
alphabetical luck rather than a guarantee. kbox's GStreamer imports are all
lazy, which hides that ordering dependency instead of making it fail
obviously.

No-op off macOS, and idempotent, so calling it here does not conflict with
anything else that calls it.
"""

from kbox.platform import configure_macos_gstreamer_env

configure_macos_gstreamer_env()
