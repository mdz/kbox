"""
Scheduling-priority helper for CPU-intensive background analysis passes.
"""

import logging
import os

logger = logging.getLogger(__name__)


def lower_priority():
    """Set this thread's scheduling priority to niced (absolute, not relative).

    Uses setpriority rather than os.nice(): this can be called repeatedly on
    a long-lived thread (e.g. ContentMonitor), and os.nice()'s increments
    would stack indefinitely. Best-effort -- unavailable on Windows.
    """
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except (OSError, AttributeError) as e:
        logger.warning("Could not lower thread priority: %s", e)
