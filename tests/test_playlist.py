from gridmosaic.playlist import Entry, Playlist, expand_inputs, read_gpls, write_gpls

SAMPLE = """#GRIDPLAYER
#P:{"grid_state":{"mode":"auto_cols","is_fit":true,"size":4},"seek_sync_mode":"disabled"}
#V0:{"id":"f671a173-23be-4bdc-99f3-8bc6e10c292b","color":"white","is_muted":true,"rate":1.0}
#V1:{"id":"5331a70f-134c-4bad-8e90-fadb6c4bd035","is_muted":false,"volume":0.5}
/videos/a.mp4
/videos/b b.mp4
"""


def test_read_gridplayer_playlist(tmp_path):
    p = tmp_path / "x.gpls"
    p.write_text(SAMPLE)
    pl = read_gpls(p)
    assert [e.path for e in pl.entries] == ["/videos/a.mp4", "/videos/b b.mp4"]
    assert pl.entries[0].muted is True
    assert pl.entries[1].muted is False and pl.entries[1].volume == 0.5
    assert pl.entries[0].extra["id"] == "f671a173-23be-4bdc-99f3-8bc6e10c292b"
    assert pl.params["grid_state"]["size"] == 4


def test_round_trip_keeps_unknown_fields(tmp_path):
    p = tmp_path / "x.gpls"
    p.write_text(SAMPLE)
    pl = read_gpls(p)
    pl.entries[0].paused = True
    out = tmp_path / "y.gpls"
    write_gpls(out, pl)
    again = read_gpls(out)
    assert again.entries[0].paused is True
    assert again.entries[0].extra == pl.entries[0].extra
    assert again.params == pl.params
    assert out.read_text().splitlines()[0] == "#GRIDPLAYER"


def test_expand_folders_recursively(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    for name in ["a.mp4", "sub/b.MKV", "sub/deeper/c.webm", "sub/notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    entries, _ = expand_inputs([tmp_path])
    names = sorted(e.path.rsplit("/", 1)[1] for e in entries)
    assert names == ["a.mp4", "b.MKV", "c.webm"]


def test_write_minimal(tmp_path):
    out = tmp_path / "new.gpls"
    write_gpls(out, Playlist([Entry("/v/1.mp4")]))
    pl = read_gpls(out)
    assert pl.entries[0].path == "/v/1.mp4"


def test_positions_round_trip(tmp_path):
    out = tmp_path / "p.gpls"
    write_gpls(out, Playlist([Entry("/v/1.mp4", position=12.5)]))
    assert '"current_position":12500' in out.read_text()
    assert read_gpls(out).entries[0].position == 12.5
    write_gpls(out, Playlist([Entry("/v/1.mp4", position=12.5)]), save_positions=False)
    assert "current_position" not in out.read_text()


def test_relative_paths_resolve_against_playlist(tmp_path):
    (tmp_path / "videos").mkdir()
    p = tmp_path / "list.gpls"
    p.write_text("#GRIDPLAYER\n#P:{}\nvideos/a.mp4\n")
    assert read_gpls(p).entries[0].path == str(tmp_path / "videos" / "a.mp4")
