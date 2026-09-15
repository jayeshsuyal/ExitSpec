"""Private stdlib-only session anchor for one trusted local invocation.

The owner must keep this process unreaped until group cleanup is verified.
Launchers must not escape the owned session/group. This is not a sandbox.
"""

import os
import select
import signal
import struct
import subprocess
import sys


def main() -> None:
    status_fd, owner_fd = map(int, sys.argv[1:3])
    # A caught handler resets on exec; SIG_IGN would leak into the producer.
    signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        child = subprocess.Popen(sys.argv[3:], stdin=subprocess.DEVNULL, close_fds=True)
    except OSError:
        os.write(status_fd, b"F" + struct.pack("!i", 0))
        child = None
    reported = child is None
    while True:
        readable, _, _ = select.select([owner_fd], [], [], 0.02)
        if readable and not os.read(owner_fd, 1):
            # Owner death/control-pipe loss: kill our group, including ourselves.
            os.killpg(os.getpgrp(), signal.SIGKILL)
        if not reported and child.poll() is not None:
            os.write(status_fd, b"R" + struct.pack("!i", child.returncode))
            reported = True
        # Remain the live group leader after the actual launcher has exited.


if __name__ == "__main__":
    try:
        main()
    finally:
        # Includes a broken status pipe or an unexpected internal exception.
        os.killpg(os.getpgrp(), signal.SIGKILL)
