import exitspec.cli as cli_module
from exitspec.wave1_execution import Wave1ProviderExecutionConfiguration


API_KEY_MARKER = "fw_test_cli_private_marker"


class _FakeServer:
    def __init__(
        self,
        *,
        enabled: bool,
        configured: bool,
        live_stt: bool = False,
    ):
        self.server_port = 8765
        self.wave1_provider_execution = _FakeExecutionStatus(
            enabled=enabled,
            configured=configured,
        )
        self.stt_demo_runtime = _FakeSTTRuntime(live_stt)
        self.serve_calls = 0
        self.close_calls = 0

    def serve_forever(self):
        self.serve_calls += 1

    def server_close(self):
        self.close_calls += 1


class _FakeExecutionStatus:
    def __init__(self, *, enabled: bool, configured: bool):
        self._status = {
            "enabled": enabled,
            "configured": configured,
        }

    def public_status(self):
        return dict(self._status)


class _FakeSTTRuntime:
    def __init__(self, live_provider_enabled: bool):
        self.live_provider_enabled = live_provider_enabled


def test_cli_does_not_read_environment_credential_without_explicit_flag(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("FIREWORKS_API_KEY", API_KEY_MARKER)
    captured = {}
    fake_server = _FakeServer(enabled=False, configured=False)

    def fake_serve_demo(**kwargs):
        captured.update(kwargs)
        return fake_server

    monkeypatch.setattr(cli_module, "serve_demo", fake_serve_demo)

    assert cli_module.main(
        ["serve", "--port", "0", "--output-dir", str(tmp_path)]
    ) == 0
    assert captured["enable_fireworks"] is False
    assert captured["fireworks_api_key"] is None
    assert captured["enable_fireworks_stt"] is False
    assert captured["fireworks_stt_api_key"] is None
    assert fake_server.serve_calls == 1
    assert fake_server.close_calls == 1
    assert API_KEY_MARKER not in capsys.readouterr().out


def test_cli_reads_server_credential_only_for_explicit_enablement(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("FIREWORKS_API_KEY", API_KEY_MARKER)
    captured = {}
    fake_server = _FakeServer(enabled=True, configured=True)

    def fake_serve_demo(**kwargs):
        captured.update(kwargs)
        return fake_server

    monkeypatch.setattr(cli_module, "serve_demo", fake_serve_demo)

    assert cli_module.main(
        [
            "serve",
            "--port",
            "0",
            "--output-dir",
            str(tmp_path),
            "--enable-fireworks",
        ]
    ) == 0
    assert captured["enable_fireworks"] is True
    assert captured["fireworks_api_key"] == API_KEY_MARKER
    assert captured["enable_fireworks_stt"] is False
    assert captured["fireworks_stt_api_key"] is None
    assert fake_server.serve_calls == 1
    assert fake_server.close_calls == 1
    assert API_KEY_MARKER not in capsys.readouterr().out


def test_cli_reads_credential_for_explicit_fireworks_stt_only(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("FIREWORKS_API_KEY", API_KEY_MARKER)
    captured = {}
    fake_server = _FakeServer(
        enabled=False,
        configured=False,
        live_stt=True,
    )

    def fake_serve_demo(**kwargs):
        captured.update(kwargs)
        return fake_server

    monkeypatch.setattr(cli_module, "serve_demo", fake_serve_demo)

    assert cli_module.main(
        [
            "serve",
            "--port",
            "0",
            "--output-dir",
            str(tmp_path),
            "--enable-fireworks-stt",
        ]
    ) == 0
    assert captured["enable_fireworks"] is False
    assert captured["fireworks_api_key"] is None
    assert captured["enable_fireworks_stt"] is True
    assert captured["fireworks_stt_api_key"] == API_KEY_MARKER
    output = capsys.readouterr().out
    assert "Experimental Fireworks STT enabled" in output
    assert API_KEY_MARKER not in output


def test_cli_reports_stt_fallback_without_rendering_missing_credential(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    fake_server = _FakeServer(enabled=False, configured=False)
    monkeypatch.setattr(cli_module, "serve_demo", lambda **_: fake_server)

    assert cli_module.main(
        [
            "serve",
            "--port",
            "0",
            "--output-dir",
            str(tmp_path),
            "--enable-fireworks-stt",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "Fireworks STT requested but not configured" in output


def test_provider_execution_configuration_representation_is_content_free():
    configuration = Wave1ProviderExecutionConfiguration(
        enabled=True,
        api_key=API_KEY_MARKER,
    )

    assert configuration.public_status() == {
        "enabled": True,
        "configured": True,
    }
    assert API_KEY_MARKER not in repr(configuration)
    assert "api_key=<redacted>" in repr(configuration)


def test_invalid_server_credential_stays_unconfigured_and_redacted():
    invalid_marker = API_KEY_MARKER + " with-whitespace"
    configuration = Wave1ProviderExecutionConfiguration(
        enabled=True,
        api_key=invalid_marker,
    )

    assert configuration.public_status() == {
        "enabled": True,
        "configured": False,
    }
    assert invalid_marker not in repr(configuration)
