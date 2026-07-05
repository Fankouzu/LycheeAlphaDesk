import json
from pathlib import Path

from lychee_alphadesk.core.evidence import build_news_evidence_pack


def test_news_evidence_pack_filters_recommendation_noise(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "news-events.json").write_text(
        json.dumps(
            {
                "provider": "newsapi",
                "rows": [
                    {
                        "timestamp": "2026-07-03T07:59:56Z",
                        "headline": "The AI infrastructure boom is sending these stocks soaring",
                        "summary": "Data center and chip demand are driving market attention.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/ai-infra",
                    },
                    {
                        "timestamp": "2026-07-03T16:47:00Z",
                        "headline": "JPMorgan names 2 Strong Buy picks for the rest of 2026",
                        "summary": "Analyst ratings and direct picks.",
                        "symbols": ["MARKET"],
                        "source_url": "https://example.com/strong-buy",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack = build_news_evidence_pack(tmp_path)

    assert len(pack) == 1
    assert pack[0].id == "news_001"
    assert pack[0].provider == "newsapi"
    assert pack[0].headline == "The AI infrastructure boom is sending these stocks soaring"
    assert pack[0].source_url == "https://example.com/ai-infra"
    assert "ai_infrastructure" in pack[0].tags
