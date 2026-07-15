import pandas as pd

from aiquanttrader.indicators import enrich_indicators


def test_enrich_indicators_adds_expected_columns():
    frame = pd.DataFrame(
        {
            "open": [1, 2, 3, 4, 5] * 50,
            "high": [2, 3, 4, 5, 6] * 50,
            "low": [0.5, 1.5, 2.5, 3.5, 4.5] * 50,
            "close": [1.2, 2.2, 3.2, 4.2, 5.2] * 50,
            "volume": [100, 110, 120, 130, 140] * 50,
        }
    )
    enriched = enrich_indicators(frame)
    assert "RSI_14" in enriched.columns
    assert "MACD" in enriched.columns
