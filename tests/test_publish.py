import copy

from novel_narrator.config import DEFAULTS
from novel_narrator.publish import abs_api
from novel_narrator.publish.abs_api import libraries_sharing_folder, server_folder
from novel_narrator.publish.abs_server import local_files, outbox_dir, outbox_root, publish_root

MOUNTS = {
    "/audiobooks": "/srv/abs/audiobooks",
    "/podcasts": "/srv/abs/podcasts",
}


def config_with(**abs_values):
    config = copy.deepcopy(DEFAULTS)
    config["abs"].update(abs_values)
    return config


def test_container_folder_maps_to_server_folder():
    assert server_folder("/audiobooks", MOUNTS) == "/srv/abs/audiobooks"
    assert server_folder("/audiobooks/Laura", MOUNTS) == "/srv/abs/audiobooks/Laura"
    assert server_folder("/audiobooks2", MOUNTS) is None
    assert server_folder("/other", MOUNTS) is None


def test_longest_mount_prefix_wins():
    assert server_folder("/audiobooks/laura/x", {"/audiobooks": "/a", "/audiobooks/laura": "/b"}) == "/b/x"


def test_detects_libraries_sharing_a_folder():
    libraries = [
        {"id": "1", "name": "Topiinho", "folders": ["/audiobooks"]},
        {"id": "2", "name": "Laura", "folders": ["/audiobooks"]},
        {"id": "3", "name": "Podcasts", "folders": ["/podcasts"]},
        {"id": "4", "name": "Sub", "folders": ["/audiobooks/sub"]},
    ]
    assert libraries_sharing_folder(libraries, libraries[1], "/audiobooks") == ["Topiinho", "Sub"]
    assert libraries_sharing_folder(libraries, libraries[2], "/podcasts") == []


def test_each_remote_destination_has_its_own_outbox():
    mine = outbox_dir(config_with(ssh="me@server", remote_dir="/srv/abs/audiobooks"))
    theirs = outbox_dir(config_with(ssh="me@server", remote_dir="/srv/abs/audiobooks_laura"))
    assert mine != theirs
    assert "audiobooks_laura_" in theirs
    assert mine == outbox_dir(config_with(ssh="me@server", remote_dir="/srv/abs/audiobooks"))


def test_local_destination_writes_straight_to_the_library():
    config = config_with(ssh="", local_dir="/mnt/abs")
    assert outbox_dir(config) == outbox_root(config)
    assert publish_root(config) == "/mnt/abs"


def test_upload_skips_partial_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "ok.m4b").write_bytes(b"1234")
    (tmp_path / "a" / "cut.m4b.part").write_bytes(b"12")
    assert local_files(str(tmp_path)) == {"a/ok.m4b": 4}


def track(index, start, duration, name):
    return {"index": index, "startOffset": start, "duration": duration, "metadata": {"filename": f"{name}.m4b"}}


class FakeAbs:
    def __init__(self, tracks_per_poll, chapters=()):
        self.tracks_per_poll = list(tracks_per_poll)
        self.chapters = list(chapters)
        self.posted = []
        self.scans = []

    def __call__(self, abs_cfg, method, path, **kwargs):
        if path.endswith("/items"):
            return {"results": [{"id": "li_1", "relPath": "Autor/Novel/Vol. 8 - Novel Volume 8"}]}
        if path.endswith("/scan"):
            self.scans.append(path)
            return {}
        if method == "POST":
            self.posted.append(kwargs["json"]["chapters"])
            return {}
        tracks = self.tracks_per_poll.pop(0) if len(self.tracks_per_poll) > 1 else self.tracks_per_poll[0]
        return {"media": {"tracks": tracks, "chapters": self.chapters}}


TRACKS = [track(1, 0, 546.2, "1591 - Capitulo 1591 - Anos Depois"), track(2, 546.2, 568, "1592 - Capitulo 1592 - X")]
FOLDER = {"Autor/Novel/Vol. 8 - Novel Volume 8": 2}


def test_chapters_follow_tracks_after_the_scan_finishes(monkeypatch):
    fake = FakeAbs(
        [TRACKS[:1], TRACKS],
        chapters=[{"id": 0, "start": 0, "end": 546.2, "title": "1591 - Capitulo 1591 - Anos Depois"}],
    )
    monkeypatch.setattr(abs_api, "_api", fake)
    assert abs_api.sync_chapters({}, {"id": "lib"}, FOLDER, sleep=lambda s: None) == 1
    assert fake.scans == ["items/li_1/scan"]
    assert [c["title"] for c in fake.posted[0]] == ["1591 - Capitulo 1591 - Anos Depois", "1592 - Capitulo 1592 - X"]
    assert fake.posted[0][1]["start"] == 546.2 and fake.posted[0][1]["end"] == 1114.2


def test_chapters_already_matching_are_left_alone(monkeypatch):
    fake = FakeAbs([TRACKS], chapters=abs_api.chapters_from_tracks(TRACKS))
    monkeypatch.setattr(abs_api, "_api", fake)
    assert abs_api.sync_chapters({}, {"id": "lib"}, FOLDER, sleep=lambda s: None) == 0
    assert fake.posted == []


def test_gives_up_when_the_scan_takes_too_long(monkeypatch):
    fake = FakeAbs([TRACKS[:1]])
    monkeypatch.setattr(abs_api, "_api", fake)
    ticks = iter(range(0, 1000, 10))
    updated = abs_api.sync_chapters(
        {}, {"id": "lib"}, FOLDER, wait_s=30, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    assert updated == 0 and fake.posted == []
