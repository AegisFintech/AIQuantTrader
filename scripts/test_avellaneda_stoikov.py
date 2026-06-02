import pandas as pd
import logging
from finrobot.strategies.avellaneda_stoikov import AvellanedaStoikovConfig, backtest_avellaneda_stoikov

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_avellaneda_stoikov():
    # Load sample data
    try:
        # XAUUSD1.csv seems to be tab-separated without header
        df = pd.read_csv("data/XAUUSD1.csv", sep='\t', header=None, 
                         names=['time', 'open', 'high', 'low', 'close', 'volume'])
        logger.info(f"Loaded {len(df)} rows of XAUUSD data")
    except Exception as e:
        logger.error(f"Failed to load data: {e}")
        return

    # Create config
    config = AvellanedaStoikovConfig(
        gamma=0.1,
        kappa=1.5,
        horizon_bars=1440,
        volatility_period=20,
        max_inventory=10,
        base_order_size=0.1,  # Larger for Gold
        debug=False
    )

    # Run backtest
    results = backtest_avellaneda_stoikov(df, config)

    # Print results
    print("\n" + "="*40)
    print("AVELLANEDA-STOIKOV TEST RESULTS")
    print("="*40)
    print(f"Total Return: {results['total_return']:.2%}")
    print(f"Max Drawdown: {results['max_drawdown']:.2%}")
    print(f"Number of Trades: {results['num_trades']}")
    print("="*40)

if __name__ == "__main__":
    test_avellaneda_stoikov()
