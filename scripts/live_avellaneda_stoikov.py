import time
import json
import os
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from finrobot.strategies.avellaneda_stoikov import AvellanedaStoikovConfig

# Paths to MT5 bridge files
BRIDGE_DIR = "/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/"
STATUS_FILE = os.path.join(BRIDGE_DIR, "finrobot_status.json")
COMMAND_FILE = os.path.join(BRIDGE_DIR, "finrobot_commands.csv")
POSITIONS_FILE = os.path.join(BRIDGE_DIR, "finrobot_positions.csv")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("live_as_trader")

class LiveAvellanedaStoikov:
    def __init__(self, config: AvellanedaStoikovConfig, symbols=["XAUUSD", "BTCUSD"]):
        self.config = config
        self.symbols = symbols
        self.state = {sym: {
            'inventory': 0.0,
            'volatility_history': [],
            'last_price': None,
            'last_vol_update': 0
        } for sym in symbols}
        self.last_command_id = int(time.time())
        
    def get_mt5_status(self):
        try:
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error reading status file: {e}")
        return None

    def get_current_inventory(self, symbol):
        try:
            if os.path.exists(POSITIONS_FILE):
                df = pd.read_csv(POSITIONS_FILE)
                if not df.empty:
                    sym_pos = df[df['symbol'] == symbol]
                    buy_vol = sym_pos[sym_pos['type'] == 'BUY']['volume'].sum()
                    sell_vol = sym_pos[sym_pos['type'] == 'SELL']['volume'].sum()
                    return buy_vol - sell_vol
        except Exception as e:
            logger.error(f"Error reading positions file: {e}")
        return 0.0

    def send_command(self, action, symbol, side, lot, sl=0.0, tp=0.0):
        self.last_command_id += 1
        command = f"{self.last_command_id},{action},{symbol},{side},{lot},{sl},{tp},20,AS_LIVE_{self.last_command_id}\n"
        try:
            with open(COMMAND_FILE, 'a') as f:
                f.write(command)
            logger.info(f"Sent command: {command.strip()}")
        except Exception as e:
            logger.error(f"Error writing command: {e}")

    def update_volatility(self, symbol, current_price):
        s = self.state[symbol]
        if s['last_price'] is not None:
            diff = current_price - s['last_price']
            s['volatility_history'].append(diff)
            if len(s['volatility_history']) > self.config.volatility_period:
                s['volatility_history'].pop(0)
        
        s['last_price'] = current_price
        
        if len(s['volatility_history']) >= 2:
            return np.std(s['volatility_history'])
        return None

    def run(self):
        logger.info("Starting Multi-Symbol Live Avellaneda-Stoikov Trader...")
        logger.info(f"Symbols: {self.symbols}")
        
        while True:
            status = self.get_mt5_status()
            if not status:
                time.sleep(1)
                continue
                
            for symbol in self.symbols:
                # Find symbol in status
                sym_status = next((s for s in status['symbols'] if s['symbol'] == symbol), None)
                if not sym_status:
                    continue
                    
                bid = sym_status['bid']
                ask = sym_status['ask']
                mid = (bid + ask) / 2
                
                s = self.state[symbol]
                
                # Update volatility once per minute
                if time.time() - s['last_vol_update'] > 60:
                    sigma = self.update_volatility(symbol, mid)
                    s['last_vol_update'] = time.time()
                    s['inventory'] = self.get_current_inventory(symbol)
                    
                    if sigma is not None:
                        # Time remaining (rolling horizon 0.5)
                        t_rem = 0.5
                        
                        # Optimal mid-price r
                        r = mid - (s['inventory'] * self.config.gamma * (sigma ** 2) * t_rem)
                        
                        # Optimal spread delta
                        term1 = self.config.gamma * (sigma ** 2) * t_rem
                        term2 = (2 / self.config.gamma) * np.log(1 + self.config.gamma / self.config.kappa)
                        delta = term1 + term2
                        
                        p_bid = r - (delta / 2)
                        p_ask = r + (delta / 2)
                        
                        logger.info(f"{symbol} Status: Mid={mid:.2f}, Sigma={sigma:.4f}, Inv={s['inventory']}, BidT={p_bid:.2f}, AskT={p_ask:.2f}")
                        
                        # Adjusted lot size for BTC vs Gold if needed
                        lot = self.config.base_order_size
                        if symbol == "BTCUSD":
                            lot = 0.1 # Example BTC lot
                        
                        # Check execution
                        if ask <= p_bid and s['inventory'] < self.config.max_inventory:
                            logger.info(f"BUY SIGNAL {symbol}: Ask {ask:.2f} <= BidT {p_bid:.2f}")
                            self.send_command("MARKET", symbol, "BUY", lot)
                            
                        elif bid >= p_ask and s['inventory'] > -self.config.max_inventory:
                            logger.info(f"SELL SIGNAL {symbol}: Bid {bid:.2f} >= AskT {p_ask:.2f}")
                            self.send_command("MARKET", symbol, "SELL", lot)
                    else:
                        logger.info(f"Gathering {symbol} volatility... ({len(s['volatility_history'])}/{self.config.volatility_period})")

            time.sleep(1)

if __name__ == "__main__":
    config = AvellanedaStoikovConfig(
        gamma=0.1,
        kappa=1.5,
        volatility_period=20,
        max_inventory=10,
        base_order_size=0.01  # Small lot for demo
    )
    trader = LiveAvellanedaStoikov(config)
    trader.run()
