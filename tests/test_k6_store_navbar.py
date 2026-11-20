"""The Catalog's navigation must be identical on every rendered page."""
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import k6_store


class Navbar(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_nav = False
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and attrs.get("aria-label") == "Main navigation":
            self.in_nav = True
        elif self.in_nav and tag == "a":
            self.links.append((attrs["href"], attrs.get("aria-current"), ""))

    def handle_data(self, data):
        if self.in_nav and self.links:
            href, active, label = self.links[-1]
            self.links[-1] = (href, active, label + data.strip())

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_nav = False


def test_navbar_is_shared_across_all_pages():
    class Store:
        def catalog(self):
            return []

        def by_id(self, repo_id):
            return dict(id=repo_id, title="Demo", source="local", commit="local",
                        screenshots=[], readme="", summary="", bundle=None)

    class Jobs:
        def get(self, job_id):
            return dict(id=job_id, state="failed", packs=["base"], exit_code=1, log="")

        def get_radio(self, job_id, radio_id):
            return dict(action="preview", state="success", log="")

    app = k6_store.create_app(Store())
    app.config["build_jobs"] = Jobs()
    client = app.test_client()
    expected = [
        ("/#catalog", "Catalog"),
        ("/profiles", "Feature packs"),
        ("/themes", "Theme editor"),
        ("/memories", "Memories"),
        ("/radio", "My radio"),
        ("/settings", "Device settings"),
    ]
    for url, section in [
        ("/", 0), ("/repo/demo", 0), ("/profiles", 1),
        ("/profiles/build/demo", 1), ("/profiles/build/demo/radio/demo", 1),
        ("/themes", 2), ("/memories", 3), ("/radio", 4), ("/settings", 5),
    ]:
        response = client.get(url)
        assert response.status_code == 200
        links = Navbar(response.get_data(as_text=True)).links
        expected_links = expected[:-1] + [("/settings?build=demo", "Device settings")] if url.startswith("/profiles/build/demo") else expected
        assert [(href, label) for href, _, label in links] == expected_links
        assert [i for i, (_, active, _) in enumerate(links) if active] == [section]
        assert len(links) == 6
    assert b'action="/refresh"' in client.get("/").data
    # Invalid settings POST and radio status POST retain the correct section.
    response = client.post("/settings", data={})
    assert response.status_code == 400
    assert Navbar(response.get_data(as_text=True)).links[5][1]
