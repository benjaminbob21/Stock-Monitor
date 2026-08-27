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
    # A strongly positive + strongly negative pair is a genuine disagreement: the
    # aggregate sign alone would mislead, so the report says "mixed".
    assert report.label == "mixed"
    assert report.mixed is True


def test_analyze_ticker_consensus_is_not_mixed() -> None:
    items = [
        NewsItem("Stock soars on record profit", "", "x", None),
        NewsItem("Shares rally on upbeat guidance", "", "y", None),
    ]
    report = analyze_ticker("AAA", FakeNewsProvider(items), VaderAnalyzer(), 7)
    assert report.label == "positive"
    assert report.mixed is False


def test_contrast_aware_scoring_reads_trailing_verdict() -> None:
    analyzer = VaderAnalyzer()
    headline = "Tesla: Potential Merger Has Some Upside, But I'm Still Bearish (Upgrade)"
    from stock_monitor.sentiment import score_with_contrast

    score = score_with_contrast(analyzer, headline)
    # The author's concluding stance ("Still Bearish") must dominate the leading
    # "Upside"/"(Upgrade)" tokens — this is the reported green-light bug.
    assert score < -0.15


class _NeutralFallbackAnalyzer(VaderAnalyzer):
    """Mimics FinBERT: neutral top-label on each clause AND on the whole text,
    with underlying positive/negative classes fighting (like the real headline)."""

    def score(self, text: str) -> float:
        if text in (
            "Tesla: Potential Merger Has Some Upside,",
            "I'm Still Bearish (Upgrade)",
        ):
            return 0.0
        return 0.05

    def torn_verdict(self, text: str) -> bool:
        return text == "I'm Still Bearish (Upgrade)"


def test_contrast_conclusion_decides_when_both_sides_read_neutral() -> None:
    """FinBERT on the real Tesla headline: every pass reads neutral-leaning-positive.

    The concluding clause carries the author's verdict, so when the whole-text
    fallback is ALSO uninformative (neutral), the conclusion must decide.
    """
    from stock_monitor.sentiment import score_with_contrast

    headline = "Tesla: Potential Merger Has Some Upside, But I'm Still Bearish (Upgrade)"
    score = score_with_contrast(_NeutralFallbackAnalyzer(), headline)
    # Tail is torn (pos/neg classes fight under a neutral top label), so the
    # concluding clause must decide — not the confident-positive whole-text pass.
    # The fake tail scores exactly 0.0, and 0.75*0 + 0.25*(positive lead) clamps
    # at >= 0 — the point is the tail now DECIDES, so assert not-positive.
    assert score <= 0.0


def test_empty_news_is_neutral() -> None:
    report = analyze_ticker("AAA", FakeNewsProvider([]), VaderAnalyzer(), 7)
    assert report.count == 0
    assert report.score == 0.0
    assert report.label == "neutral"


def test_negative_news_forces_sell_signal() -> None:
    assert exit_signal(90, ["negative_news"]) == "consider selling"
