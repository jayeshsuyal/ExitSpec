"""Private, bounded pipe transport to the fixed app-owned Zoom child.

The same OS user and repository installation are trusted. No HTTP/browser input
selects executable paths, environment or credentials. Stderr is discarded, never
forwarded into application errors. There is no transcript disk recorder.
"""

from __future__ import annotations

import json
import queue
import shutil
import struct
import subprocess
import threading
from pathlib import Path

MAX_IPC_FRAME = 128 * 1024


def encode_frame(value: dict) -> bytes:
    body = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not 1 <= len(body) <= MAX_IPC_FRAME:
        raise ValueError("Zoom child frame is outside its bounds.")
    return struct.pack(">I", len(body)) + body


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate IPC field.")
        value[key] = item
    return value


def read_frame(stream) -> dict:
    def exact(size):
        chunks = bytearray()
        while len(chunks) < size:
            piece = stream.read(size - len(chunks))
            if not piece:
                raise EOFError
            chunks.extend(piece)
        return bytes(chunks)

    length = struct.unpack(">I", exact(4))[0]
    if not 1 <= length <= MAX_IPC_FRAME:
        raise ValueError("Zoom child frame is outside its bounds.")
    value = json.loads(exact(length).decode("utf-8"), object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError("Zoom child frame is invalid.")
    return value


class ZoomPipeChild:
    """One private child; send is bounded/nonblocking and never performs I/O."""

    def __init__(self, init: dict, receive, failed):
        node = shutil.which("node")
        runner = (
            Path(__file__).resolve().parents[2]
            / "tools/zoom_fixture_operator/live-child.mjs"
        )
        if not node or not runner.is_file():
            raise ValueError("Zoom operator runtime is unavailable.")
        init = dict(init)
        expected_revision = init.pop("_code_revision", None)
        root = runner.parents[2]
        # Run outside workflow locks, immediately before loading child code.
        observed = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=root, stderr=subprocess.DEVNULL
        )
        if observed != expected_revision or dirty:
            raise ValueError("Zoom operator candidate changed.")
        self._queue = queue.Queue(maxsize=8)
        self._closed = threading.Event()
        self._process = subprocess.Popen(
            [node, str(runner)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            close_fds=True,
            cwd=runner.parent,
        )

        def reader():
            try:
                while not self._closed.is_set():
                    receive(read_frame(self._process.stdout))
            except (OSError, ValueError, EOFError, RecursionError):
                if not self._closed.is_set():
                    failed()
            finally:
                self.close()

        def writer():
            try:
                while not self._closed.is_set():
                    try:
                        frame = self._queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    self._process.stdin.write(frame)
                    self._process.stdin.flush()
            except (OSError, ValueError):
                if not self._closed.is_set():
                    failed()
            finally:
                self.close()

        self.send(init)
        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=writer, daemon=True).start()

    def send(self, message):
        if self._closed.is_set():
            raise ValueError("Zoom child is closed.")
        self._queue.put_nowait(encode_frame(message))

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        # Kill first to unblock pipe readers/writers, then reap out of caller locks.
        try:
            self._process.kill()
        except OSError:
            pass

        def reap():
            self._process.wait()
            self._process.stdin.close()
            self._process.stdout.close()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

        threading.Thread(target=reap, daemon=True).start()
