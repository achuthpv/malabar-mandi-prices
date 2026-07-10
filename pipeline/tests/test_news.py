"""News fetch/merge/publish tests (no network — fixture RSS)."""

from mandi.news import fetch_feed, merge
from mandi.publish import _news_items

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>test feed</title>
  <item>
    <title>Arecanut prices surge on supply squeeze</title>
    <link>https://example.com/a1</link>
    <source url="https://example.com">Example News</source>
    <pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>No link item is dropped</title>
    <link>javascript:alert(1)</link>
    <pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Bad date still kept</title>
    <link>https://example.com/a2</link>
    <pubDate>not a date</pubDate>
  </item>
</channel></rss>"""


class FakeResp:
    content = RSS.encode()
    def raise_for_status(self):
        pass


class FakeSession:
    def get(self, url, timeout=None):
        return FakeResp()


def test_fetch_feed_parses_and_drops_bad_links():
    items = fetch_feed("arecanut price India", FakeSession())
    titles = [i["title"] for i in items]
    assert "Arecanut prices surge on supply squeeze" in titles
    assert "No link item is dropped" not in titles  # javascript: URL rejected
    assert all(i["url"].startswith("http") for i in items)
    assert items[0]["date"] == "2026-07-08"


def test_merge_dedupes_prunes_and_sorts():
    existing = [
        {"date": "2026-07-01", "title": "Old but kept", "source": "s",
         "url": "https://e.com/1", "fetched_at": "x"},
        {"date": "2020-01-01", "title": "Ancient pruned", "source": "s",
         "url": "https://e.com/2", "fetched_at": "x"},
        {"date": "2026-07-01", "title": "Dup Title", "source": "s",
         "url": "https://e.com/3", "fetched_at": "x"},
    ]
    fresh = [
        {"date": "2026-07-08", "title": "dup   title", "source": "s2",
         "url": "https://e.com/4"},  # same title normalized -> fresh wins
        {"date": "2026-07-08", "title": "Brand new", "source": "s2",
         "url": "https://e.com/5"},
    ]
    merged = merge(existing, fresh, "2026-07-08T10:00:00+00:00", "2026-07-08")
    titles = [m["title"] for m in merged]
    assert titles == ["dup   title", "Brand new", "Old but kept"]
    assert merged[0]["source"] == "s2"  # fresh copy kept for the dup


def test_news_items_publishes_recent_valid_only(tmp_path):
    (tmp_path / "arecanut.csv").write_text(
        "date,title,source,url,fetched_at\n"
        "2026-07-08,Fresh headline,Src,https://e.com/1,x\n"
        "2026-05-01,Too old,Src,https://e.com/2,x\n"
        "2026-07-07,Bad url,Src,ftp://nope,x\n"
    )
    items = _news_items("arecanut", "2026-07-09T00:00:00+00:00", tmp_path)
    assert [i["title"] for i in items] == ["Fresh headline"]
    assert _news_items("no-such-slug", "2026-07-09T00:00:00+00:00", tmp_path) == []
