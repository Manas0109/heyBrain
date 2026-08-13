"""macOS notification adapter (plan.md §11).

The only supported delivery mechanism is AppleScript's `display
notification` via `osascript`. macOS only -- there is no cross-platform
fallback in this MVP.
"""

from __future__ import annotations

import subprocess

_TIMEOUT_SECONDS = 5


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str) -> None:
    """Fire a macOS notification banner. Never raises on failure."""
    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        # Best-effort delivery -- a missing osascript (non-macOS) or a
        # transient failure should never crash the tick loop.
        pass
