"""Bounded synthetic terminal input and one-server operator composition."""
import copy
from types import SimpleNamespace

import pytest

from exitspec import source_authoring_launch as launch
from exitspec import source_authoring_operator as operator
from tests.helpers.source_authoring_admission import install_fake_profile


@pytest.fixture
def tty(monkeypatch):
    original = [0, 0, 0, operator.termios.ECHO | operator.termios.ICANON, 0, 0, [0] * 32]
    state = SimpleNamespace(attrs=copy.deepcopy(original), data=bytearray(), effects=[], fault=None, reads=0)
    monkeypatch.setattr(operator.os, "open", lambda *args: 91)
    monkeypatch.setattr(operator.os, "isatty", lambda fd: state.fault != "not_tty")
    monkeypatch.setattr(operator.termios, "tcgetattr", lambda fd: copy.deepcopy(state.attrs))

    def settings(fd, when, attrs):
        state.effects.append(("settings", copy.deepcopy(attrs)))
        if state.fault == "set_failed" and len(state.effects) == 1:
            raise operator.termios.error("PRIVATE-MARKER")
        if state.fault != "echo_retained" or attrs == original:
            state.attrs = copy.deepcopy(attrs)

    def read(fd, maximum):
        assert fd == 91 and maximum == 1
        state.reads += 1
        if state.fault == "interrupt":
            raise KeyboardInterrupt()
        value = bytes(state.data[:maximum])
        del state.data[:maximum]
        return value

    monkeypatch.setattr(operator.termios, "tcsetattr", settings)
    monkeypatch.setattr(operator.os, "read", read)
    monkeypatch.setattr(operator.os, "write", lambda fd, wire: len(wire))
    monkeypatch.setattr(operator.os, "close", lambda fd: state.effects.append(("close", fd)))
    monkeypatch.setattr(operator.select, "select", lambda r, w, x, timeout:
                        ([], w, []) if state.fault == "timeout" else (r, w, []))
    state.original = original
    return state


@pytest.mark.parametrize("size", [1, 4096])
def test_unchanged_credential_bounds_restore_terminal(tty, size):
    tty.data.extend(b"K" * size + b"\n")
    assert operator._read_credential_tty() == b"K" * size
    assert tty.reads == size + 1
    assert tty.effects[-1] == ("close", 91) and tty.attrs == tty.original
    assert not tty.effects[0][1][3] & (operator.termios.ECHO | operator.termios.ICANON)


@pytest.mark.parametrize("raw", [b"\n", b"K" * 4097 + b"\n", b" KEY\n", b"KEY \n",
                                  b"KEY\r\n", b"KEY\x00\n", b"KEY\xff\n", b"KEY\t\n"])
def test_invalid_secret_never_gets_repaired_or_echoed(tty, raw, capsys):
    tty.data.extend(raw)
    with pytest.raises(launch.SourceAuthoringLaunchError) as error:
        operator._read_credential_tty()
    assert "KEY" not in str(error.value)
    assert tty.reads <= 4097 and tty.attrs == tty.original
    assert tty.effects[-1] == ("close", 91)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("fault", ["not_tty", "set_failed", "echo_retained", "timeout", "interrupt", "eof"])
def test_terminal_failures_refuse_without_fallback(tty, fault):
    tty.fault = fault
    with pytest.raises(launch.SourceAuthoringLaunchError):
        operator._read_credential_tty()
    assert tty.effects[-1] == ("close", 91)
    assert tty.attrs == tty.original


@pytest.mark.parametrize("args", [[], ["--help"], ["--approval-id", "x", "--key", "PRIVATE-MARKER"],
                                   ["--approval-id", "x", "--port", "0"],
                                   ["--approval-id", "x", "--port", "65536"],
                                   ["--approval-id", "x", "--port", "NaN"],
                                   ["--approval-id", "x", "--output-root", "relative"],
                                   ["--approval-id", "x", "--output-root", "/tmp/../secret"],
                                   ["--approval-id", "x", "--profile", "PRIVATE-MARKER"]])
def test_cli_refuses_untrusted_shapes_without_echo(monkeypatch, args, capsys):
    install_fake_profile(monkeypatch)
    effects = []
    monkeypatch.setattr(operator, "_read_text_tty", lambda *_: effects.append(True))
    assert operator.main(args) == 2 and effects == []
    assert "PRIVATE-MARKER" not in capsys.readouterr().out


def test_operator_admits_before_secret_and_pairs_same_source_neutral_server(monkeypatch, tmp_path):
    profile = install_fake_profile(monkeypatch)
    effects, installed = [], []

    class Server:
        server_port = 8765

        def serve_forever(self):
            effects.append("serve")

        def shutdown(self):
            effects.append("shutdown")

        def server_close(self):
            effects.append("close")

    server = Server()

    def serve(**kwargs):
        assert effects[:2] == ["approved", "credential"]
        handle = kwargs["source_authoring_launch"]
        assert launch._LAUNCHES[handle].credential == b"SYNTHETIC-KEY"
        installed.append(handle)
        effects.append("construct")
        return server

    def pair(current, revision, **kwargs):
        assert current is server and revision == profile.code_revision
        assert kwargs["read_text"] is operator._read_text_tty and kwargs["secret"] is operator._read_text_tty
        effects.append("pair")
        return True

    def text(prompt):
        if "APPROVED" in prompt:
            effects.append("approved")
            return "APPROVED"
        effects.append("stop")
        return ""

    def credential():
        effects.append("credential")
        return b"SYNTHETIC-KEY"

    monkeypatch.setattr(operator, "serve_source_neutral_demo", serve)
    monkeypatch.setattr(operator, "_pair_zoom_in_server", pair)
    monkeypatch.setattr(operator, "_read_text_tty", text)
    monkeypatch.setattr(operator, "_read_credential_tty", credential)
    assert operator.main(["--approval-id", profile.approval_id, "--output-root", str(tmp_path)]) == 0
    assert effects.count("construct") == effects.count("pair") == effects.count("close") == 1
    assert launch._LAUNCHES[installed[0]].state == "REVOKED"
    assert launch._LAUNCHES[installed[0]].credential == b""


@pytest.mark.parametrize("ending,accepted", [(b"\n", True), (b"\r\n", False)])
def test_real_posix_terminal_hides_input_and_restores_attributes(monkeypatch, ending, accepted):
    import os
    import pty
    import select
    import threading

    master, slave = pty.openpty()
    original_attributes = operator.termios.tcgetattr(slave)
    original_open = os.open
    results = []

    def open_tty(path, flags, *args, **kwargs):
        return os.dup(slave) if path == "/dev/tty" else original_open(path, flags, *args, **kwargs)

    def read():
        try:
            results.append(operator._read_credential_tty())
        except launch.SourceAuthoringLaunchError:
            results.append(None)

    monkeypatch.setattr(operator.os, "open", open_tty)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        assert select.select([master], [], [], 1)[0]
        prompt = os.read(master, 4096)
        assert b"Fireworks key" in prompt
        assert not operator.termios.tcgetattr(slave)[3] & (operator.termios.ECHO | operator.termios.ICANON)
        os.write(master, b"SYNTHETIC-KEY" + ending)
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert results == ([b"SYNTHETIC-KEY"] if accepted else [None])
        restored = operator.termios.tcgetattr(slave)
        # macOS sets the kernel PENDIN bit when canonical mode is restored.
        # It is transient reprocessing state, not a changed input/echo setting.
        restored[3] &= ~getattr(operator.termios, "PENDIN", 0)
        original_attributes[3] &= ~getattr(operator.termios, "PENDIN", 0)
        assert restored == original_attributes
        assert not select.select([master], [], [], 0.02)[0]  # Secret and LF were not echoed.
    finally:
        os.close(master)
        os.close(slave)
        thread.join(timeout=1)
