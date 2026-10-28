import copy

from novel_narrator.config import DEFAULTS
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
