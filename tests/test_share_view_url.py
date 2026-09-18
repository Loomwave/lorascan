from lorascan.share import share_view_url


def test_bare_host_maps_to_root():
    assert share_view_url("https://share.lorascan.app") == "https://share.lorascan.app/"
    assert share_view_url("https://share.lorascan.app/") == "https://share.lorascan.app/"


def test_post_route_is_stripped_to_the_map():
    assert share_view_url("https://share.lorascan.app/v1/share") == "https://share.lorascan.app/"
    assert share_view_url("http://10.0.0.5:8081/v1/share/") == "http://10.0.0.5:8081/"


def test_empty_endpoint_gives_nothing_to_print():
    assert share_view_url("") == ""
    assert share_view_url(None) == ""


def test_readme_tells_people_where_to_look():
    import pathlib
    readme = pathlib.Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text()
    assert "## The community map (share.lorascan.app)" in text
    assert "https://share.lorascan.app/v1/dump.json" in text
    assert "is being deployed" not in text
