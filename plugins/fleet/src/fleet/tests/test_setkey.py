"""`fleet set`: one settings key set everywhere, validated first, its value never shown."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from fleet import setkey, shell, sync, validators

SECRET = "s3cret-value-1234"


class Host(sync.Machine):
    """A host that is another home directory on this machine: the same commands, run without ssh."""

    def __init__(self, name: str, home):
        super().__init__(name)
        self.home = home

    def run(self, argv, input=None, check=True):
        proc = shell.run(argv, cwd=self.home, check=False, input=input or "")
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"{self.name}: `{' '.join(argv)}` failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        return proc


def _settings(home, settings: dict | None = None) -> dict:
    path = home / sync.SETTINGS_JSON
    if settings is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings))
    return json.loads(path.read_text())


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """This machine and two hosts, with no validators unless a test adds one."""
    monkeypatch.setattr(validators, "VALIDATORS", {})
    here = tmp_path / "here"
    monkeypatch.setenv("HOME", str(here))
    _settings(here, {"env": {"KEY": "old", "OTHER": "laptop"}, "model": "opus", "statusLine": {"command": "x"}})
    hosts = []
    for name in ("u@a", "u@b"):
        home = tmp_path / name
        _settings(home, {"env": {"KEY": "old", "OTHER": "host", "DISPLAY": ":99"}, "model": "sonnet"})
        hosts.append(Host(name, home))
    return [sync.Machine(None), *hosts]


def _homes(machines) -> list:
    return [Path.home(), *(m.home for m in machines[1:])]


def test_set_writes_the_key_everywhere_and_only_that_key(fleet, capsys):
    assert setkey.set_key(fleet, "env.KEY", SECRET) == 0
    here, a, b = (_settings(h) for h in _homes(fleet))
    assert here == {"env": {"KEY": SECRET, "OTHER": "laptop"}, "model": "opus", "statusLine": {"command": "x"}}
    assert a == b == {"env": {"KEY": SECRET, "OTHER": "host", "DISPLAY": ":99"}, "model": "sonnet"}
    for home in _homes(fleet):
        [backup] = (home / ".claude").glob("settings.json.bak-*")
        assert json.loads(backup.read_text())["env"]["KEY"] == "old"
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "no validator for env.KEY" in out
    assert f"sha256 {setkey.fingerprint(SECRET)}" in out
    assert out.count("  ok      ") == 3


def test_set_to_the_current_value_writes_nothing(fleet):
    setkey.set_key(fleet, "env.KEY", SECRET)
    for home in _homes(fleet):
        for backup in (home / ".claude").glob("settings.json.bak-*"):
            backup.unlink()
    assert setkey.set_key(fleet, "env.KEY", SECRET) == 0
    for home in _homes(fleet):
        assert not list((home / ".claude").glob("settings.json.bak-*"))


def test_set_a_new_top_level_key_creates_it(fleet):
    assert setkey.set_key(fleet, "apiKeyHelper", SECRET) == 0
    assert all(_settings(h)["apiKeyHelper"] == SECRET for h in _homes(fleet))


def test_set_creates_a_missing_settings_file_on_a_host(fleet):
    (fleet[2].home / sync.SETTINGS_JSON).unlink()
    assert setkey.set_key(fleet, "env.KEY", SECRET) == 0
    assert _settings(fleet[2].home) == {"env": {"KEY": SECRET}}


def test_a_host_that_fails_is_reported_and_the_others_still_get_the_value(fleet, capsys):
    (fleet[1].home / sync.SETTINGS_JSON).write_text("{ not json")
    assert setkey.set_key(fleet, "env.KEY", SECRET) == 1
    assert _settings(fleet[2].home)["env"]["KEY"] == SECRET
    out = capsys.readouterr().out
    assert "FAILED  u@a: " in out
    assert "  ok      u@b\n" in out
    assert "Failed on u@a:" in out
    assert SECRET not in out


def test_a_rejected_value_is_written_nowhere(fleet, monkeypatch, capsys):
    def reject(value, settings):
        raise validators.Rejected("HTTP 401: bad key")

    monkeypatch.setitem(validators.VALIDATORS, "env.KEY", reject)
    with pytest.raises(validators.Rejected, match="401"):
        setkey.set_key(fleet, "env.KEY", SECRET)
    for home in _homes(fleet):
        assert _settings(home)["env"]["KEY"] == "old"
        assert not list((home / ".claude").glob("settings.json.bak-*"))
    assert SECRET not in capsys.readouterr().out


def test_a_validator_gets_the_value_and_this_machines_settings(fleet, monkeypatch, capsys):
    seen = []
    monkeypatch.setitem(validators.VALIDATORS, "env.KEY", lambda value, settings: seen.append((value, settings)))
    assert setkey.set_key(fleet, "env.KEY", SECRET) == 0
    assert seen == [
        (SECRET, {"env": {"KEY": "old", "OTHER": "laptop"}, "model": "opus", "statusLine": {"command": "x"}})
    ]
    assert "env.KEY: accepted by <lambda>" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("key", "error"),
    [("statusLine", "is a dict, not a string"), ("model.x", "`model` in .* is not an object")],
)
def test_set_refuses_to_replace_what_is_not_a_string(fleet, key, error):
    with pytest.raises(RuntimeError, match=error):
        setkey.set_key(fleet, key, SECRET)


def test_check_key_refuses_a_machine_key_and_a_malformed_path():
    config = sync.Config(hosts=["u@h"], machine_settings=["env.DISPLAY", "statusLine"])
    setkey.check_key("env.AWS_BEARER_TOKEN_BEDROCK", config)
    for key in ("env.DISPLAY", "statusLine.command"):
        with pytest.raises(RuntimeError, match="each machine's own"):
            setkey.check_key(key, config)
    for key in ("", "env.", ".X"):
        with pytest.raises(RuntimeError, match="not a settings key path"):
            setkey.check_key(key, config)


def test_read_value_from_stdin_drops_the_final_newline(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(SECRET + "\n"))
    assert setkey.read_value("env.KEY") == SECRET


@pytest.mark.parametrize(("given", "error"), [("", "no value"), ("\n", "no value"), ("a\nb\n", "several lines")])
def test_read_value_refuses_an_empty_or_multi_line_value(monkeypatch, given, error):
    monkeypatch.setattr("sys.stdin", io.StringIO(given))
    with pytest.raises(RuntimeError, match=error):
        setkey.read_value("env.KEY")


def test_read_value_from_a_terminal_prompts_without_echo(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("getpass.getpass", lambda prompt: SECRET)
    assert setkey.read_value("env.KEY") == SECRET


# --- the gateway validator, against a local stand-in for the gateway ---


@pytest.fixture
def gateway():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, self.headers["Authorization"], body))
            ok = self.headers["Authorization"] == f"Bearer {SECRET}"
            answer = b'{"content": []}' if ok else f'{{"message": "bad key {self.headers["Authorization"]}"}}'.encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/bedrock", calls
    server.shutdown()


def _gateway_settings(base: str) -> dict:
    return {"env": {"ANTHROPIC_BEDROCK_BASE_URL": base, "ANTHROPIC_DEFAULT_HAIKU_MODEL": "us.anthropic.claude-x[1m]"}}


def test_gateway_validator_makes_one_tiny_call_with_the_new_key(gateway):
    base, calls = gateway
    validators.bedrock_gateway(SECRET, _gateway_settings(base))
    [(path, auth, body)] = calls
    assert path == "/bedrock/model/us.anthropic.claude-x/invoke"
    assert auth == f"Bearer {SECRET}"
    assert body["max_tokens"] == 1


def test_gateway_validator_rejects_a_bad_key_without_showing_it(gateway):
    base, _ = gateway
    with pytest.raises(validators.Rejected, match="HTTP 401") as exc:
        validators.bedrock_gateway("wrong-key", _gateway_settings(base))
    assert "wrong-key" not in str(exc.value)
    assert "<new value>" in str(exc.value)


def test_gateway_validator_without_the_gateway_settings_refuses():
    with pytest.raises(RuntimeError, match="no env.ANTHROPIC_BEDROCK_BASE_URL"):
        validators.bedrock_gateway(SECRET, {"env": {}})


def test_the_gateway_key_has_the_gateway_validator():
    assert validators.VALIDATORS["env.AWS_BEARER_TOKEN_BEDROCK"] is validators.bedrock_gateway
