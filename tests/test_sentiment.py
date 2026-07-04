"""News + sentiment tests (network-free via a fake news provider)."""

from __future__ import annotations

from stock_monitor.positions import exit_signal
from stock_monitor.sentiment import (
    NewsItem,
    NewsProvider,
    VaderAnalyzer,
    analyze_ticker,
)


class FakeNewsProvider(NewsProvider):
    name = "fake-news"

    def __init__(self, items: list[NewsItem]) -> None:
        self._items = items

    def get_news(self, ticker: str, lookback_days: int) -> list[NewsItem]:
        return list(self._items)


def test_vader_finance_lexicon_reads_headlines() -> None:
    analyzer = VaderAnalyzer()
    assert analyzer.score("Company beats earnings, stock soars") > 0.2
    assert analyzer.score("Firm slashes guidance amid fraud probe") < -0.2


def test_analyze_ticker_aggregates_and_labels() -> None:
    items = [
        NewsItem("Stock soars on record profit", "", "x", None),
        NewsItem("Shares plunge on fraud probe", "", "y", None),
    ]
    report = analyze_ticker("AAA", FakeNewsProvider(items), VaderAnalyzer(), 7)
    assert report.count == 2
    assert -1.0 <= report.score <= 1.0
    assert all(i.sentiment is not None for i in report.items)
    assert report.label in {"positive", "neutral", "negative"}


def test_empty_news_is_neutral() -> None:
    report = analyze_ticker("AAA", FakeNewsProvider([]), VaderAnalyzer(), 7)
    assert report.count == 0
    assert report.score == 0.0
    assert report.label == "neutral"


def test_negative_news_forces_sell_signal() -> None:
    assert exit_signal(90, ["negative_news"]) == "consider selling"
