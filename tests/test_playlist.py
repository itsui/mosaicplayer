import os

from mosaicplayer.playlist import Entry, Playlist, expand_inputs, read_gpls, write_gpls

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


def test_size_weight_round_trip(tmp_path):
    out = tmp_path / "w.gpls"
    write_gpls(out, Playlist([Entry("/v/1.mp4", size_weight=2.0), Entry("/v/2.mp4")]))
    entries = read_gpls(out).entries
    assert entries[0].size_weight == 2.0 and entries[1].size_weight == 1.0
    assert out.read_text().count("gm_size_weight") == 1


def test_damaged_playlist_lines_are_skipped(tmp_path):
    p = tmp_path / "bad.gpls"
    p.write_text("#GRIDPLAYER\n#P:{not json\n#V0:{broken\n#Vx:{}\n#V1:{\"gm_size_weight\": 0}\n"
                 "#V2:{\"rate\": \"fast\", \"current_position\": null}\n/a.mp4\n/b.mp4\n/c.mp4\n")
    pl = read_gpls(p)
    assert [e.path for e in pl.entries] == ["/a.mp4", "/b.mp4", "/c.mp4"]
    assert pl.params == {}
    assert pl.entries[1].size_weight == 0.25  # clamped: zero would break the layout
    assert pl.entries[2].rate == 1.0 and pl.entries[2].position == 0.0


def test_write_is_atomic(tmp_path):
    out = tmp_path / "x.gpls"
    write_gpls(out, Playlist([Entry("/v/1.mp4")]))
    write_gpls(out, Playlist([Entry("/v/2.mp4")]))
    assert read_gpls(out).entries[0].path == "/v/2.mp4"
    assert [f.name for f in tmp_path.iterdir()] == ["x.gpls"]  # no temp files left


def test_macos_metadata_files_are_not_videos(tmp_path):
    (tmp_path / "._clip.mp4").write_bytes(b"")
    (tmp_path / "clip.mp4").write_bytes(b"")
    entries, _ = expand_inputs([tmp_path])
    assert [e.path.rsplit("/", 1)[1] for e in entries] == ["clip.mp4"]


def test_byte_order_marks_and_windows_line_ends(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"")
    gp = tmp_path / "bom.gpls"
    gp.write_text("#GRIDPLAYER\r\n#P:{}\r\n" + str(tmp_path / "a.mp4") + "\r\n", encoding="utf-8-sig")
    assert [e.path for e in read_gpls(gp).entries] == [str(tmp_path / "a.mp4")]
    m3 = tmp_path / "bom.m3u"
    m3.write_text("#EXTM3U\r\n#EXTINF:-1,Clip\r\na.mp4\r\n", encoding="utf-8-sig")
    entries, _ = expand_inputs([m3])
    assert [e.path for e in entries] == [str(tmp_path / "a.mp4")]


def test_latin1_m3u(tmp_path):
    (tmp_path / "vidéo.mp4").write_bytes(b"")  # the file on disk has a UTF-8 name
    m3 = tmp_path / "old.m3u"
    m3.write_bytes("#EXTM3U\nvidéo.mp4\n".encode("cp1252"))  # written by an old Windows tool
    entries, _ = expand_inputs([m3])
    assert [e.path for e in entries] == [str(tmp_path / "vidéo.mp4")]


def test_file_uris_become_paths(tmp_path):
    from mosaicplayer.playlist import expand_inputs, local_path, read_m3u
    video = tmp_path / "my clip é.mp4"
    video.write_bytes(b"")
    uri = video.as_uri()  # file:///…/my%20clip%20%C3%A9.mp4, as VLC writes them
    assert local_path(uri) == str(video)
    assert local_path("file://localhost" + uri[7:]) == str(video)
    assert local_path("https://example.com/a%20b.mp4") == "https://example.com/a%20b.mp4"
    m3u = tmp_path / "vlc.m3u"
    m3u.write_text(f"#EXTM3U\n#EXTINF:5,clip\n{uri}\n")
    assert [e.path for e in read_m3u(m3u).entries] == [str(video)]
    assert [e.path for e in expand_inputs([uri])[0]] == [str(video)]


def test_folder_scan_skips_hidden_folders_and_sorts_naturally(tmp_path):
    from mosaicplayer.playlist import expand_inputs
    for name in ["clip 10.mp4", "clip 2.mp4", "Clip 1.mp4", ".Trash-1000/files/old.mp4",
                 "sub/.thumbs/t.mp4", "sub/a.mp4"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"")
    names = [os.path.relpath(e.path, tmp_path) for e in expand_inputs([tmp_path])[0]]
    assert names == ["Clip 1.mp4", "clip 2.mp4", "clip 10.mp4", os.path.join("sub", "a.mp4")]
    hidden = tmp_path / ".Trash-1000"  # chosen directly, a hidden folder is scanned
    assert len(expand_inputs([hidden])[0]) == 1


def test_saving_keeps_permissions_and_symlinks(tmp_path):
    from mosaicplayer.playlist import Entry, Playlist, read_gpls, write_gpls
    real = tmp_path / "real.gpls"
    write_gpls(real, Playlist([Entry("/v/a.mp4")]))
    os.chmod(real, 0o644)
    link = tmp_path / "link.gpls"
    link.symlink_to(real)
    write_gpls(link, Playlist([Entry("/v/a.mp4"), Entry("/v/b.mp4")]))
    assert link.is_symlink()
    assert len(read_gpls(real).entries) == 2
    assert (real.stat().st_mode & 0o777) == 0o644


def test_next_in_folder(tmp_path):
    from mosaicplayer.playlist import next_in_folder
    for name in ["clip 1.mp4", "clip 2.mp4", "clip 10.mp4", "notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    d = str(tmp_path)
    assert next_in_folder(f"{d}/clip 2.mp4") == f"{d}/clip 10.mp4"
    assert next_in_folder(f"{d}/clip 10.mp4") == f"{d}/clip 1.mp4"  # wraps around
    assert next_in_folder(f"{d}//./clip 1.mp4") == f"{d}/clip 2.mp4"  # spelled differently
    assert next_in_folder(f"{d}/clip 5.mp4") == f"{d}/clip 10.mp4"  # renamed or deleted since
    assert next_in_folder(f"{d}/missing/x.mp4") is None
