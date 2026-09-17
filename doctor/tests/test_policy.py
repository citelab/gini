from gini_doctor.stage1 import policy


def test_without_a_health_center_the_builtin_policy_is_complete(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.HEALTHCENTER_ENV, raising=False)
    p = policy.load(cache_directory=str(tmp_path))
    assert p.source == policy.BUILTIN_SOURCE and p.note is None
    assert p.get("min_python") == "3.8" and "system" in p.get("groups_default")


def test_live_policy_wins_and_is_cached_for_the_next_offline_run(tmp_path, hc):
    server = hc({"version": "hc-3", "min_python": "3.10", "probe_timeout_s": 45})
    live = policy.load(healthcenter=server.url, cache_directory=str(tmp_path))
    assert (live.source, live.version, live.get("min_python")) == ("live", "hc-3", "3.10")
    server.close()

    offline = policy.load(healthcenter=server.url, cache_directory=str(tmp_path), timeout=1)
    assert (offline.source, offline.version) == ("cached", "hc-3")
    assert "unavailable" in offline.note


def test_offline_flag_never_contacts_the_health_center(tmp_path, hc):
    server = hc({"version": "hc-9"})
    p = policy.load(healthcenter=server.url, cache_directory=str(tmp_path), offline=True)
    assert p.source == "builtin"


def test_bad_fields_fall_back_individually():
    merged = policy.validate({"version": 4, "min_python": "three", "groups_default": "system",
                              "probe_timeout_s": True, "surprise": "ignored"})
    assert merged["version"] == "4"
    assert merged["min_python"] == policy.BUILTIN["min_python"]
    assert merged["groups_default"] == policy.BUILTIN["groups_default"]
    assert merged["probe_timeout_s"] == policy.BUILTIN["probe_timeout_s"]
    assert "surprise" not in merged


def test_a_garbage_response_does_not_stop_the_doctor(tmp_path, hc):
    server = hc(b"<html>proxy login</html>")
    p = policy.load(healthcenter=server.url, cache_directory=str(tmp_path))
    assert p.source == "builtin" and "unavailable" in p.note


def test_cache_location_follows_each_platform_convention(monkeypatch):
    monkeypatch.delenv(policy.CACHE_DIR_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\x\\AppData\\Local")
    assert policy.cache_dir("windows").endswith("gini-doctor")
    assert "Library" in policy.cache_dir("macos")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg")
    assert policy.cache_dir("linux").replace("\\", "/") == "/tmp/xdg/gini-doctor"
