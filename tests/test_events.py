import datetime as dt

from stock_monitor.events import EventRecord, as_utc, classify_headline, dedupe_events


def _event(**kwargs) -> EventRecord:
    values = {
        "ticker": "mRNA",
        "headline": "  FDA   approval announced ",
        "source": "News",
        "published_at": dt.datetime(2026, 8, 1, 12),
        "known_at": dt.datetime(2026, 8, 1, 12, 1),
        "url": "https://NEWS.example/story?id=1&utm_source=x",
    }
    values.update(kwargs)
    return EventRecord(**values)


def test_as_utc_treats_naive_provider_timestamp_as_utc() -> None:
    result = as_utc(dt.datetime(2026, 8, 1, 12))
    assert result.tzinfo == dt.UTC
    assert result.hour == 12


def test_dedupe_keeps_earliest_known_copy_and_normalizes() -> None:
    later = _event(known_at=dt.datetime(2026, 8, 1, 12, 2))
    earlier = _event(known_at=dt.datetime(2026, 8, 1, 12, 1))
    result = dedupe_events([later, earlier])
    assert len(result) == 1
    assert result[0].known_at.minute == 1
    assert result[0].headline == "FDA approval announced"
    assert result[0].url == "https://news.example/story"


def test_classify_headline_returns_material_event_category() -> None:
    assert classify_headline("Company reports earnings beat and raises guidance") == (
        "earnings",
        0.75,
    )
