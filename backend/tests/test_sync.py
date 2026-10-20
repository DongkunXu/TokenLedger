from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn

from tokenledger import ledger
from tokenledger.config import Machine, Paths, Settings, save_settings
from tokenledger.models import CaptureResult, CostQuality, DayUsage, QuotaSample, Scope, SeriesKey
from tokenledger.store import connect, migrate, repo
from tokenledger.sync import client, discovery, envelope, snapshot
from tokenledger.sync import config as sync_config

from conftest import utc

TZ = "Europe/London"
HUB = Machine("hub-mac", "Hub Mac", "darwin")
NODE = Machine("win-box", "Windows box", "windows")


def make_settings(machine: Machine, tz: str = TZ) -> Settings:
    return Settings(timezone=tz, grace_days=3, machine=machine)


def open_db(path):
    conn = connect(path)
    migrate(conn)
    return conn


def usage(day, total, model="*", cost=None):
    quality = CostQuality.LIST_PRICE if cost is not None else CostQuality.NONE
    return DayUsage(day=day, model=model, total=total, cost_micros=cost, cost_quality=quality)


def capture_into(conn, settings, series, rows, now, window=None):
    result = CaptureResult(
        series=series,
        rows=rows,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        tool_version="t",
        raw_name="t",
        raw=b"",
    )
    with repo.transaction(conn):
        repo.ensure_machine(conn, settings.machine, now)
        cid = repo.start_capture(conn, "usage", series.source, repo.series_id(conn, series), now)
        ledger.apply_capture(conn, result, settings=settings, now_utc=utc(now), capture_id=cid)
        repo.finish_capture(conn, cid, now, status="ok", tool_version="t")


def node_series(machine=NODE.id):
    return SeriesKey("claude-logs", "claude", machine, Scope.MACHINE)


ACCOUNT = SeriesKey("codex-server", "codex", "acct-1", Scope.ACCOUNT)


@pytest.fixture
def node(tmp_path):
    conn = open_db(tmp_path / "node.sqlite")
    yield conn
    conn.close()


@pytest.fixture
def hub(tmp_path):
    conn = open_db(tmp_path / "hub.sqlite")
    yield conn
    conn.close()


def push_snapshot(node_conn, hub_conn, *, node_settings=None, hub_settings=None, taken_at=None):
    snap = snapshot.build(node_conn, node_settings or make_settings(NODE), app_version="test", taken_at=taken_at)
    snap = json.loads(json.dumps(snap))  # what travels is JSON
    with repo.transaction(hub_conn):
        return snapshot.apply(
            hub_conn, snap, sender=snap["machine"]["id"], settings=hub_settings or make_settings(HUB), now="2026-10-20T12:00:00+00:00"
        )


# -- key and envelope ---------------------------------------------------------------------


def test_key_round_trip_and_forms():
    key = sync_config.new_key()
    assert key.startswith("tlk1-")
    raw = sync_config.parse_key(key)
    assert sync_config.parse_key(key.lower().replace("-", " ")) == raw
    assert sync_config.Keys.derive(key) == sync_config.Keys.derive(key.upper())
    assert sync_config.Keys.derive(key).key_id != sync_config.Keys.derive(sync_config.new_key()).key_id
    with pytest.raises(sync_config.SyncConfigError):
        sync_config.parse_key("tlk1-ABCD")


def test_config_file_is_private(tmp_path):
    paths = Paths(data=tmp_path)
    cfg = sync_config.SyncConfig(role="hub", key=sync_config.new_key(), networks=("192.168.1.0/24",))
    sync_config.save(paths, cfg)
    assert sync_config.load(paths) == cfg
    assert (sync_config.config_path(paths).stat().st_mode & 0o077) == 0
    with pytest.raises(sync_config.SyncConfigError):
        sync_config.SyncConfig(role="hub", key=cfg.key, networks=("not-a-net",))


def test_envelope_round_trip_and_tampering():
    keys = sync_config.Keys.derive(sync_config.new_key())
    headers, body = envelope.new_request(keys.encryption, "/p", "m1", {"a": 1}, now=1000)
    assert envelope.open_request(keys.encryption, "/p", headers, body, now=1000) == ("m1", headers[envelope.H_REQUEST], {"a": 1})
    with pytest.raises(envelope.EnvelopeError):  # another path
        envelope.open_request(keys.encryption, "/q", headers, body, now=1000)
    with pytest.raises(envelope.EnvelopeError):  # another machine claims it
        envelope.open_request(keys.encryption, "/p", {**headers, envelope.H_MACHINE: "m2"}, body, now=1000)
    with pytest.raises(envelope.EnvelopeError):  # altered
        envelope.open_request(keys.encryption, "/p", headers, body[:-1] + bytes([body[-1] ^ 1]), now=1000)
    other = sync_config.Keys.derive(sync_config.new_key())
    with pytest.raises(envelope.EnvelopeError):
        envelope.open_request(other.encryption, "/p", headers, body, now=1000)
    with pytest.raises(envelope.ClockSkewError):
        envelope.open_request(keys.encryption, "/p", headers, body, now=1000 + 3600)


def test_response_is_bound_to_its_request():
    keys = sync_config.Keys.derive(sync_config.new_key())
    sealed = envelope.seal_response(keys.encryption, "/p", "m1", "req-1", {"ok": True})
    assert envelope.open_response(keys.encryption, "/p", "m1", "req-1", sealed) == {"ok": True}
    with pytest.raises(envelope.EnvelopeError):
        envelope.open_response(keys.encryption, "/p", "m1", "req-2", sealed)


# -- discovery ----------------------------------------------------------------------------


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_network_filter():
    assert discovery.allowed("192.168.1.20", ())
    assert discovery.allowed("127.0.0.1", ("10.0.0.0/8",))  # loopback always
    assert not discovery.allowed("8.8.8.8", ())
    assert discovery.allowed("10.1.2.3", ("10.0.0.0/8",))
    assert not discovery.allowed("192.168.1.20", ("10.0.0.0/8",))
    assert discovery.allowed("::ffff:192.168.1.5", ())


def test_discovery_finds_only_the_hub_with_our_key():
    port = free_port(socket.SOCK_DGRAM)
    mine = sync_config.Keys.derive(sync_config.new_key())
    theirs = sync_config.Keys.derive(sync_config.new_key())
    responder = discovery.Responder(keys=mine, machine="hub-mac", label="Hub", port=port, version="t", host="127.0.0.1").start()
    try:
        found = discovery.discover(port=port, keys=mine, targets=["127.0.0.1"], timeout=1.0)
        assert [(h.machine, h.host, h.port, h.verified) for h in found] == [("hub-mac", "127.0.0.1", port, True)]
        assert discovery.discover(port=port, keys=theirs, targets=["127.0.0.1"], timeout=0.5) == []
        listed = discovery.discover(port=port, keys=None, targets=["127.0.0.1"], timeout=1.0)
        assert [(h.machine, h.key_id, h.verified) for h in listed] == [("hub-mac", mine.key_id, False)]
    finally:
        responder.stop()


def test_forged_answer_is_rejected():
    keys = sync_config.Keys.derive(sync_config.new_key())
    answer = discovery.answer_for(
        discovery.ASK + json.dumps({"nonce": "n1", "key_id": keys.key_id}).encode(),
        keys=keys, machine="hub", label="Hub", port=1, version="t",
    )
    assert discovery._parse_answer(answer, "10.0.0.2", "n1", keys).verified
    assert discovery._parse_answer(answer, "10.0.0.2", "other-nonce", keys) is None  # replayed answer


# -- snapshots ----------------------------------------------------------------------------


def seed_node(node, settings=None):
    settings = settings or make_settings(NODE)
    capture_into(node, settings, node_series(), [usage("2026-10-10", 100, cost=5), usage("2026-10-10", 100, "opus", 5),
                                                 usage("2026-10-19", 40, cost=2)], "2026-10-19T10:00:00")
    capture_into(node, settings, ACCOUNT, [usage("2026-10-10", 900), usage("2026-10-19", 300)], "2026-10-19T10:00:00")
    repo.insert_quota_samples(node, [QuotaSample("claude", "a", "weekly", 10080, "2026-10-19T10:00:00Z", 12.5, None, "codexbar-quota")])


def test_snapshot_mirrors_the_node_and_is_idempotent(node, hub):
    seed_node(node)
    first = push_snapshot(node, hub, taken_at="2026-10-19T10:00:01.000000+00:00")
    assert first["inserted"] == 5 and first["captures"] == 2 and first["quota"] == 1 and not first["stale"]
    rows = hub.execute(
        "SELECT s.source, s.machine, l.day, l.model, l.total, l.state, l.origin FROM ledger l JOIN series s ON s.id = l.series_id "
        "ORDER BY s.source, l.day, l.model"
    ).fetchall()
    assert [(r["source"], r["day"], r["model"], r["total"], r["state"]) for r in rows] == [
        ("claude-logs", "2026-10-10", "*", 100, "final"),
        ("claude-logs", "2026-10-10", "opus", 100, "final"),
        ("claude-logs", "2026-10-19", "*", 40, "open"),
        ("codex-server", "2026-10-10", "*", 900, "final"),
        ("codex-server", "2026-10-19", "*", 300, "open"),
    ]
    assert all(r["origin"].startswith("sync:win-box:capture:") for r in rows)
    machine = hub.execute("SELECT * FROM machines WHERE id = ?", (NODE.id,)).fetchone()
    assert machine["label"] == "Windows box" and machine["last_snapshot_at"] == "2026-10-19T10:00:01.000000+00:00"
    caps = hub.execute("SELECT machine, sync_ref, source FROM captures ORDER BY sync_ref").fetchall()
    assert [(c["machine"], c["source"]) for c in caps] == [(NODE.id, "claude-logs"), (NODE.id, "codex-server")]

    again = push_snapshot(node, hub, taken_at="2026-10-19T10:00:02.000000+00:00")
    assert (again["inserted"], again["updated"], again["unchanged"], again["captures"], again["quota"]) == (0, 0, 5, 0, 0)


def test_stale_snapshot_is_ignored(node, hub):
    seed_node(node)
    push_snapshot(node, hub, taken_at="2026-10-19T10:00:05.000000+00:00")
    late = push_snapshot(node, hub, taken_at="2026-10-19T10:00:01.000000+00:00")
    assert late == {"stale": True, "applied_snapshot": "2026-10-19T10:00:05.000000+00:00"}


def test_later_changes_reach_the_hub(node, hub):
    settings = make_settings(NODE)
    seed_node(node, settings)
    push_snapshot(node, hub, taken_at="2026-10-19T10:00:01.000000+00:00")
    # Later on the node: the open day grows, then is settled.
    capture_into(node, settings, node_series(), [usage("2026-10-19", 70, cost=4)], "2026-10-23T09:00:00")
    result = push_snapshot(node, hub, taken_at="2026-10-23T09:00:01.000000+00:00")
    assert result["updated"] == 1
    row = hub.execute("SELECT l.total, l.state FROM ledger l JOIN series s ON s.id = l.series_id "
                      "WHERE s.source = 'claude-logs' AND l.day = '2026-10-19' AND l.model = '*'").fetchone()
    assert (row["total"], row["state"]) == (70, "final")


def test_account_rows_from_two_machines(node, hub, tmp_path):
    # The hub reads the same account itself and has already settled 10-10.
    hub_settings = make_settings(HUB)
    capture_into(hub, hub_settings, ACCOUNT, [usage("2026-10-10", 950), usage("2026-10-19", 320)], "2026-10-19T11:00:00")
    capture_into(hub, hub_settings, ACCOUNT, [usage("2026-10-10", 950)], "2026-10-20T11:00:00")
    seed_node(node)  # the node read 900 / 300 earlier
    result = push_snapshot(node, hub, taken_at="2026-10-19T10:00:01.000000+00:00")
    assert result["kept_final"] == 1  # 10-10 is final on the hub: kept
    totals = dict(hub.execute("SELECT l.day, l.total FROM ledger l JOIN series s ON s.id = l.series_id "
                              "WHERE s.source = 'codex-server'").fetchall())
    assert totals == {"2026-10-10": 950, "2026-10-19": 320}  # the node's open 10-19 reading is older
    # A newer reading from another machine replaces an open day.
    other = open_db(tmp_path / "other.sqlite")
    laptop = Machine("laptop", "Laptop", "linux")
    capture_into(other, make_settings(laptop), ACCOUNT, [usage("2026-10-19", 400)], "2026-10-20T12:00:00")
    push_snapshot(other, hub, node_settings=make_settings(laptop), taken_at="2026-10-20T12:00:01.000000+00:00")
    assert hub.execute("SELECT l.total FROM ledger l JOIN series s ON s.id = l.series_id "
                       "WHERE s.source = 'codex-server' AND l.day = '2026-10-19'").fetchone()[0] == 400


def test_refusals(node, hub):
    seed_node(node)
    with pytest.raises(snapshot.SyncRefused, match="timezone"):
        push_snapshot(node, hub, hub_settings=make_settings(HUB, tz="Asia/Shanghai"))
    with pytest.raises(snapshot.SyncRefused, match="hub's machine id"):
        push_snapshot(node, hub, node_settings=make_settings(HUB))
    snap = snapshot.build(node, make_settings(NODE), app_version="t")
    snap["series"][0]["key"][2] = "someone-else"
    with pytest.raises(snapshot.SyncRefused, match="only send its own"):
        with repo.transaction(hub):
            snapshot.apply(hub, snap, sender=NODE.id, settings=make_settings(HUB), now="2026-10-20T12:00:00+00:00")


def test_foreign_rows_are_not_sent(node):
    # A machine that used to be a hub holds other machines' rows; it only sends its own.
    settings = make_settings(NODE)
    seed_node(node, settings)
    capture_into(node, settings, node_series("old-node"), [usage("2026-10-10", 5)], "2026-10-19T10:00:00")
    snap = snapshot.build(node, settings, app_version="t")
    assert {tuple(s["key"][:3]) for s in snap["series"]} == {("claude-logs", "claude", NODE.id), ("codex-server", "codex", "acct-1")}


# -- over the network ---------------------------------------------------------------------


class HubThread:
    def __init__(self, paths: Paths, port: int):
        from tokenledger.sync.hub import create_hub_app

        self.server = uvicorn.Server(uvicorn.Config(create_hub_app(paths), host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def setup_machine(root, machine: Machine, tz=TZ) -> tuple[Paths, Settings]:
    paths = Paths(data=root)
    settings = make_settings(machine, tz)
    save_settings(paths, settings)
    return paths, settings


def test_join_and_push_over_http(tmp_path):
    hub_paths, hub_settings = setup_machine(tmp_path / "hub", HUB)
    node_paths, _ = setup_machine(tmp_path / "node", NODE, tz="America/New_York")  # adopts the hub's on join
    cfg = client.hub_init(hub_paths)
    port = free_port()
    with HubThread(hub_paths, port):
        url = f"http://127.0.0.1:{port}"
        assert client.hello(url)["machine"] == HUB.id
        node_conn = open_db(node_paths.db)
        joined = client.join(node_conn, node_paths, make_settings(NODE, "America/New_York"), key=cfg.key, hub_url=url)
        assert joined["hub_machine"] == HUB.id and joined["adopted_timezone"]
        from tokenledger.config import load_settings

        node_settings = load_settings(node_paths)
        assert node_settings.timezone == TZ
        seed_node(node_conn, node_settings)
        result = client.push(node_conn, node_paths, node_settings)
        assert result["ok"] and result["inserted"] == 5, result
        status = client.status(node_paths)
        assert status["role"] == "node" and status["last_ok"] and status["hub"] == "Hub Mac"

        # A wrong key is told apart from an unreachable hub.
        wrong = sync_config.SyncConfig(role="node", key=sync_config.new_key(), hub_url=url, hub_machine=HUB.id)
        sync_config.save(node_paths, wrong)
        failed = client.push(node_conn, node_paths, node_settings)
        assert not failed["ok"] and "key" in failed["error"]
        node_conn.close()

    hub_conn = open_db(hub_paths.db)
    assert hub_conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 5
    hub_conn.close()


def test_push_finds_a_moved_hub_by_discovery(tmp_path, monkeypatch):
    hub_paths, _ = setup_machine(tmp_path / "hub", HUB)
    node_paths, node_settings = setup_machine(tmp_path / "node", NODE)
    cfg = client.hub_init(hub_paths)
    port = free_port()
    udp = discovery.Responder(keys=cfg.keys, machine=HUB.id, label="Hub Mac", port=port, version="t", host="127.0.0.1").start()
    monkeypatch.setattr(discovery, "broadcast_targets", lambda networks: ["127.0.0.1"])
    try:
        with HubThread(hub_paths, port):
            # Joined when the hub had another address.
            stale = sync_config.SyncConfig(role="node", key=cfg.key, port=port, hub_machine=HUB.id, hub_label="Hub Mac",
                                           hub_last_url="http://127.0.0.1:1")
            sync_config.save(node_paths, stale)
            node_conn = open_db(node_paths.db)
            seed_node(node_conn, node_settings)
            result = client.push(node_conn, node_paths, node_settings, discover_timeout=1.0)
            assert result["ok"] and result["url"] == f"http://127.0.0.1:{port}", result
            assert sync_config.load(node_paths).hub_last_url == f"http://127.0.0.1:{port}"
            node_conn.close()
    finally:
        udp.stop()


def test_unreachable_hub_is_recorded_not_raised(tmp_path):
    node_paths, node_settings = setup_machine(tmp_path / "node", NODE)
    sync_config.save(node_paths, sync_config.SyncConfig(role="node", key=sync_config.new_key(), hub_url="http://127.0.0.1:1"))
    conn = open_db(node_paths.db)
    result = client.after_capture(conn, node_paths, node_settings)
    assert result["ok"] is False and "no answer" in result["error"]
    assert client.status(node_paths)["last_ok"] is False
    conn.close()


def test_hub_started_on_a_copied_ledger_attributes_old_captures(tmp_path):
    from tokenledger.sync.hub import create_hub_app

    paths, settings = setup_machine(tmp_path / "hub", HUB)
    conn = open_db(paths.db)
    old = make_settings(Machine("old-hub", "Old hub", "darwin"))
    capture_into(conn, old, node_series("old-hub"), [usage("2026-10-10", 5)], "2026-10-19T10:00:00")
    conn.close()
    client.hub_init(paths)
    create_hub_app(paths)
    conn = open_db(paths.db)
    assert [r["machine"] for r in conn.execute("SELECT machine FROM captures")] == ["old-hub"]
    conn.close()
