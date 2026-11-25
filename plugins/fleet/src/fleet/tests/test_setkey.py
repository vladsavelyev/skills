"""`fleet set`: one settings key set everywhere, validated first, its value never shown."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from fleet import mirrors, setkey, shell, sync, validators

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
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
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
    setkey.set_key(fleet, "env.KEY", SECRET, [])
    for home in _homes(fleet):
        for backup in (home / ".claude").glob("settings.json.bak-*"):
            backup.unlink()
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
    for home in _homes(fleet):
        assert not list((home / ".claude").glob("settings.json.bak-*"))


def test_set_a_new_top_level_key_creates_it(fleet):
    assert setkey.set_key(fleet, "apiKeyHelper", SECRET, []) == 0
    assert all(_settings(h)["apiKeyHelper"] == SECRET for h in _homes(fleet))


def test_set_creates_a_missing_settings_file_on_a_host(fleet):
    (fleet[2].home / sync.SETTINGS_JSON).unlink()
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
    assert _settings(fleet[2].home) == {"env": {"KEY": SECRET}}


def test_a_host_that_fails_is_reported_and_the_others_still_get_the_value(fleet, capsys):
    (fleet[1].home / sync.SETTINGS_JSON).write_text("{ not json")
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 1
    assert _settings(fleet[2].home)["env"]["KEY"] == SECRET
    out = capsys.readouterr().out
    assert "FAILED  u@a: " in out
    assert "  ok      u@b: env.KEY\n" in out
    assert "Failed on u@a:" in out
    assert SECRET not in out


def test_a_host_whose_settings_are_not_an_object_fails_alone(fleet, capsys):
    (fleet[1].home / sync.SETTINGS_JSON).write_text("[]")
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 1
    assert _settings(fleet[2].home)["env"]["KEY"] == SECRET
    assert "FAILED  u@a: ~/.claude/settings.json is not a JSON object" in capsys.readouterr().out


def test_an_edit_made_while_validating_is_kept(fleet, monkeypatch):
    def edit_meanwhile(value, settings):
        _settings(Path.home(), {**settings, "theme": "dark"})

    monkeypatch.setitem(validators.VALIDATORS, "env.KEY", edit_meanwhile)
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
    assert _settings(Path.home())["theme"] == "dark"
    assert _settings(Path.home())["env"]["KEY"] == SECRET


def test_set_keeps_a_private_files_mode(fleet):
    for home in _homes(fleet):
        (home / sync.SETTINGS_JSON).chmod(0o600)
    (fleet[2].home / sync.SETTINGS_JSON).unlink()
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
    for home in _homes(fleet):
        assert (home / sync.SETTINGS_JSON).stat().st_mode & 0o777 == 0o600


def test_write_replaces_a_read_only_file_and_keeps_it_read_only(fleet):
    path = Path.home() / sync.SETTINGS_JSON
    path.chmod(0o444)
    sync.Machine(None).write(sync.SETTINGS_JSON, "{}\n")
    assert path.read_text() == "{}\n"
    assert path.stat().st_mode & 0o777 == 0o444
    assert not list(path.parent.glob("*.fleet-new"))


def test_set_keeps_a_hosts_unrelated_empty_objects(fleet):
    _settings(fleet[1].home, {"env": {"KEY": "old"}, "permissions": {}})
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
    assert _settings(fleet[1].home) == {"env": {"KEY": SECRET}, "permissions": {}}


def test_a_rejected_value_is_written_nowhere(fleet, monkeypatch, capsys):
    def reject(value, settings):
        raise validators.Rejected("HTTP 401: bad key")

    monkeypatch.setitem(validators.VALIDATORS, "env.KEY", reject)
    with pytest.raises(validators.Rejected, match="401"):
        setkey.set_key(fleet, "env.KEY", SECRET, [])
    for home in _homes(fleet):
        assert _settings(home)["env"]["KEY"] == "old"
        assert not list((home / ".claude").glob("settings.json.bak-*"))
    assert SECRET not in capsys.readouterr().out


def test_a_validator_gets_the_value_and_this_machines_settings(fleet, monkeypatch, capsys):
    seen = []
    monkeypatch.setitem(validators.VALIDATORS, "env.KEY", lambda value, settings: seen.append((value, settings)))
    assert setkey.set_key(fleet, "env.KEY", SECRET, []) == 0
    assert seen == [
        (SECRET, {"env": {"KEY": "old", "OTHER": "laptop"}, "model": "opus", "statusLine": {"command": "x"}})
    ]
    assert "env.KEY: accepted by <lambda>" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("key", "error"),
    [("statusLine", "is of type dict, not a string"), ("model.x", "`model` in .* is not an object")],
)
def test_set_refuses_to_replace_what_is_not_a_string(fleet, key, error):
    with pytest.raises(RuntimeError, match=error):
        setkey.set_key(fleet, key, SECRET, [])


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


@pytest.mark.parametrize("given", ["", "\n"])
def test_read_value_refuses_an_empty_value(monkeypatch, given):
    monkeypatch.setattr("sys.stdin", io.StringIO(given))
    with pytest.raises(RuntimeError, match="no value"):
        setkey.read_value("env.KEY")


def test_read_value_keeps_a_multi_line_value(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a\nb\n"))
    assert setkey.read_value("env.KEY") == "a\nb"


def test_read_value_from_a_terminal_prompts_without_echo(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("getpass.getpass", lambda prompt: SECRET)
    assert setkey.read_value("env.KEY") == SECRET


# --- mirrors: copies of the value in other files ---

CODEX = """model = "gpt"

[model_providers.azure]
base_url = "https://gateway.example/openai"
http_headers = { "api-key" = "old" }  # the gateway key
query_params = { api-version = "2025" }
"""
MIRROR = mirrors.FileMirror("env.KEY", ".codex/config.toml", "model_providers.azure.http_headers.api-key")
TOML = mirrors.FileMirror("env.KEY", "f.toml", "a.key")


def _codex(home, text: str | None = CODEX):
    path = home / MIRROR.file
    if text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return path


def test_set_updates_the_mirrors_on_every_machine_and_nothing_else_in_them(fleet, capsys):
    for home in _homes(fleet):
        _codex(home)
    assert setkey.set_key(fleet, "env.KEY", SECRET, [MIRROR]) == 0
    for home in _homes(fleet):
        assert _codex(home, None).read_text() == CODEX.replace('"old"', f'"{SECRET}"')
        [backup] = (home / ".codex").glob("config.toml.bak-*")
        assert backup.read_text() == CODEX
    out = capsys.readouterr().out
    assert SECRET not in out
    assert out.count(f"  ok      u@a: {MIRROR}\n") == 1
    assert out.count(f"  ok      this machine: {MIRROR}\n") == 1


def test_a_machine_without_the_mirror_file_is_skipped(fleet, capsys):
    _codex(Path.home())
    assert setkey.set_key(fleet, "env.KEY", SECRET, [MIRROR]) == 0
    assert "u@a: no ~/.codex/config.toml" in capsys.readouterr().out
    assert not (fleet[1].home / MIRROR.file).exists()


def test_a_mirror_file_without_the_key_fails_that_machine_only(fleet, capsys):
    for home in _homes(fleet):
        _codex(home)
    _codex(fleet[1].home, 'model = "gpt"\n')
    assert setkey.set_key(fleet, "env.KEY", SECRET, [MIRROR]) == 1
    assert _settings(fleet[1].home)["env"]["KEY"] == SECRET
    assert SECRET in _codex(fleet[2].home, None).read_text()
    out = capsys.readouterr().out
    assert "FAILED  u@a: ~/.codex/config.toml: model_providers.azure.http_headers.api-key is not there" in out
    assert "Failed on u@a:" in out


def test_mirror_with_value_keeps_a_literal_string_style_file_otherwise_intact():
    text = "[a]\n# note\nkey = 'old'\nother = 'old-ish'\n"
    assert TOML.with_value(text, "new", "f") == "[a]\n# note\nkey = \"new\"\nother = 'old-ish'\n"
    assert TOML.with_value(text, "old", "f") == text


def test_mirror_with_value_writes_any_character_as_valid_toml():
    value = 'k\U0001f600\x7f\u00e9"\\\n'
    top = mirrors.FileMirror("env.KEY", "f.toml", "a")
    assert top.value_in(top.with_value('a = "old"\n', value, "f")) == value


def test_mirror_with_value_refuses_a_value_written_twice():
    with pytest.raises(RuntimeError, match="written 2 times"):
        mirrors.FileMirror("env.KEY", "f.toml", "a").with_value('a = "old"\nb = "old"\n', "new", "f")


def test_mirror_with_value_refuses_what_is_not_a_string():
    with pytest.raises(RuntimeError, match="is of type int, not a string"):
        mirrors.FileMirror("env.KEY", "f.toml", "a").with_value("a = 1\n", "new", "f")


def test_load_config_reads_mirrors():
    sync.CONFIG_PATH.parent.mkdir(parents=True)
    sync.CONFIG_PATH.write_text(
        'hosts = ["u@h"]\n[[mirrors]]\nsetting = "env.KEY"\nfile = ".codex/config.toml"\n'
        'key = "model_providers.azure.http_headers.api-key"\n'
        '[[mirrors]]\nsetting = "env.KEY"\nt3_provider = "antigravity"\nvariable = "AGY_KEY"\n'
    )
    assert sync.load_config().mirrors == [MIRROR, T3]


def test_a_file_mirror_must_be_toml_or_json():
    with pytest.raises(RuntimeError, match="only a TOML or JSON file"):
        mirrors.FileMirror("env.KEY", ".config/x.yaml", "a")


# --- a JSON file mirror, e.g. a second key in ~/.claude/settings.json holding the same value ---

JSON = mirrors.FileMirror("env.KEY", sync.SETTINGS_JSON, "env.COPY")


def test_set_updates_a_json_mirror_in_the_settings_file_itself(fleet, capsys):
    for home in _homes(fleet):
        _settings(home, {**_settings(home), "env": {**_settings(home)["env"], "COPY": "old"}})
    assert setkey.set_key(fleet, "env.KEY", SECRET, [JSON]) == 0
    for home in _homes(fleet):
        assert _settings(home)["env"]["KEY"] == _settings(home)["env"]["COPY"] == SECRET
    assert _settings(fleet[1].home)["env"]["DISPLAY"] == ":99"
    out = capsys.readouterr().out
    assert SECRET not in out
    assert out.count(f"  ok      u@b: {JSON}\n") == 1


def test_a_json_mirror_without_the_key_fails_that_machine_only(fleet, capsys):
    for home in (Path.home(), fleet[2].home):
        _settings(home, {**_settings(home), "env": {**_settings(home)["env"], "COPY": "old"}})
    assert setkey.set_key(fleet, "env.KEY", SECRET, [JSON]) == 1
    assert _settings(fleet[2].home)["env"]["COPY"] == SECRET
    assert "FAILED  u@a: ~/.claude/settings.json: env.COPY is not there" in capsys.readouterr().out


# --- a T3 Code provider's sensitive environment variable ---

T3 = mirrors.T3Mirror("env.KEY", "antigravity", "AGY_KEY")
T3_SETTINGS = {
    "providerInstances": {
        "antigravity": {
            "driver": "antigravity",
            "environment": [
                {"name": "AGY_URL", "value": "https://gw", "sensitive": False},
                {"name": "AGY_KEY", "value": "", "sensitive": True, "valueRedacted": True},
            ],
        }
    }
}


def _t3(home, settings: dict | None = T3_SETTINGS, secret: str | None = "old") -> Path:
    """A T3 Code data directory as the server leaves it: settings, and the secret store's file for AGY_KEY."""
    if settings is not None:
        path = home / mirrors.T3_SETTINGS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2))
    if secret is not None:
        path = home / T3.secret
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secret.encode())
        path.chmod(0o600)
    return home / T3.secret


def test_t3_secret_file_is_named_as_t3_names_it():
    assert T3.secret == ".t3/userdata/secrets/provider-env-YW50aWdyYXZpdHk-QUdZX0tFWQ.bin"


def test_set_writes_a_t3_providers_secret_and_makes_t3_reload_its_settings(fleet, capsys):
    settings_before = {}
    for home in _homes(fleet):
        _t3(home)
        settings_before[home] = (home / mirrors.T3_SETTINGS).stat().st_ino
    assert setkey.set_key(fleet, "env.KEY", SECRET, [T3]) == 0
    for home in _homes(fleet):
        secret = home / T3.secret
        assert secret.read_bytes() == SECRET.encode()
        assert secret.stat().st_mode & 0o777 == 0o600
        settings = home / mirrors.T3_SETTINGS
        assert json.loads(settings.read_text()) == T3_SETTINGS
        # Replaced, not edited in place: that is what makes the T3 server reload it.
        assert settings.stat().st_ino != settings_before[home]
        assert not list(settings.parent.glob("settings.json.*"))
    out = capsys.readouterr().out
    assert SECRET not in out
    assert out.count(f"  ok      this machine: {T3}\n") == 1


def test_a_t3_secret_already_set_is_left_alone(fleet, capsys):
    for home in _homes(fleet):
        _t3(home, secret=SECRET)
    inodes = {home: (home / mirrors.T3_SETTINGS).stat().st_ino for home in _homes(fleet)}
    assert setkey.set_key(fleet, "env.KEY", SECRET, [T3]) == 0
    assert all((home / mirrors.T3_SETTINGS).stat().st_ino == inodes[home] for home in _homes(fleet))
    assert capsys.readouterr().out.count(f"{T3} identical") == 3


@pytest.mark.parametrize(
    "settings",
    [None, {"providerInstances": {}}, {"providerInstances": {"antigravity": {"driver": "antigravity"}}}],
    ids=["no-t3", "no-instance", "no-variable"],
)
def test_a_machine_whose_t3_lacks_the_variable_is_skipped(fleet, capsys, settings):
    for home in (Path.home(), fleet[2].home):
        _t3(home)
    _t3(fleet[1].home, settings, secret=None)
    assert setkey.set_key(fleet, "env.KEY", SECRET, [T3]) == 0
    assert not (fleet[1].home / T3.secret).exists()
    assert (fleet[2].home / T3.secret).read_bytes() == SECRET.encode()
    out = capsys.readouterr().out
    assert (
        "u@a: no T3 Code settings" in out
        if settings is None
        else "u@a: T3 Code has no T3 provider antigravity AGY_KEY" in out
    )


def test_a_t3_variable_kept_in_plain_text_fails_that_machine(fleet, capsys):
    plain = json.loads(json.dumps(T3_SETTINGS))
    plain["providerInstances"]["antigravity"]["environment"][1] = {
        "name": "AGY_KEY",
        "value": "old",
        "sensitive": False,
    }
    for home in (Path.home(), fleet[2].home):
        _t3(home)
    _t3(fleet[1].home, plain, secret=None)
    assert setkey.set_key(fleet, "env.KEY", SECRET, [T3]) == 1
    assert (
        "FAILED  u@a: ~/.t3/userdata/settings.json: T3 provider antigravity AGY_KEY is not kept in T3's secret store"
        in (capsys.readouterr().out)
    )
    assert json.loads((fleet[1].home / mirrors.T3_SETTINGS).read_text()) == plain


def test_a_t3_secret_file_t3_never_wrote_fails_instead_of_being_created(fleet, capsys):
    for home in (Path.home(), fleet[2].home):
        _t3(home)
    _t3(fleet[1].home, secret=None)
    assert setkey.set_key(fleet, "env.KEY", SECRET, [T3]) == 1
    assert not (fleet[1].home / T3.secret).exists()
    assert "T3 may have moved its secrets" in capsys.readouterr().out


# --- the gateway validator, against a local stand-in for the gateway ---


@pytest.fixture
def gateway():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, self.headers["Authorization"], body))
            if self.path.startswith("/moved/"):
                # A redirect to somewhere that answers 200 whatever the key.
                self.send_response(307)
                self.send_header("Location", self.path.replace("/moved/", "/open/", 1))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            ok = self.path.startswith("/open/") or self.headers["Authorization"] == f"Bearer {SECRET}"
            answer = b'{"content": []}' if ok else f'{{"message": "bad key {self.headers["Authorization"]}"}}'.encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()


def _gateway_settings(base: str) -> dict:
    return {"env": {"ANTHROPIC_BEDROCK_BASE_URL": base, "ANTHROPIC_DEFAULT_HAIKU_MODEL": "us.anthropic.claude-x[1m]"}}


def test_gateway_validator_makes_one_tiny_call_with_the_new_key(gateway):
    base, calls = gateway
    validators.bedrock_gateway(SECRET, _gateway_settings(base + "/bedrock"))
    [(path, auth, body)] = calls
    assert path == "/bedrock/model/us.anthropic.claude-x/invoke"
    assert auth == f"Bearer {SECRET}"
    assert body["max_tokens"] == 1


def test_gateway_validator_rejects_a_bad_key_without_showing_it(gateway):
    base, _ = gateway
    with pytest.raises(validators.Rejected, match="HTTP 401$") as exc:
        validators.bedrock_gateway("wrong-key", _gateway_settings(base + "/bedrock"))
    assert "wrong-key" not in str(exc.value)


def test_gateway_validator_puts_an_arn_model_in_one_path_segment(gateway):
    base, calls = gateway
    settings = _gateway_settings(base + "/bedrock")
    settings["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "arn:aws:bedrock:us-east-1:1:inference-profile/x[1m]"
    validators.bedrock_gateway(SECRET, settings)
    [(path, _, _)] = calls
    assert path == "/bedrock/model/arn%3Aaws%3Abedrock%3Aus-east-1%3A1%3Ainference-profile%2Fx/invoke"


def test_gateway_validator_does_not_follow_a_redirect(gateway):
    base, calls = gateway
    with pytest.raises(validators.Rejected, match="HTTP 307"):
        validators.bedrock_gateway("wrong-key", _gateway_settings(base + "/moved"))
    assert [path for path, _, _ in calls] == ["/moved/model/us.anthropic.claude-x/invoke"]


def test_gateway_validator_rejects_a_multi_line_key_before_calling():
    with pytest.raises(validators.Rejected, match="several lines"):
        validators.bedrock_gateway("a\nb", {})


def test_gateway_validator_without_the_gateway_settings_refuses():
    with pytest.raises(RuntimeError, match="no env.ANTHROPIC_BEDROCK_BASE_URL"):
        validators.bedrock_gateway(SECRET, {"env": {}})


def test_the_gateway_key_has_the_gateway_validator():
    assert validators.VALIDATORS["env.AWS_BEARER_TOKEN_BEDROCK"] is validators.bedrock_gateway
