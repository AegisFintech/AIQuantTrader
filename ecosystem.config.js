// PM2 ecosystem for AIQuantTrader.
// Active trading is MT5 demo via AIQuantTraderBridgeEA on XAUUSD.
// All PM2 service output is intentionally consolidated into logs/combined.log.

const path = require("path");

const ROOT = __dirname;
const COMBINED_LOG = path.join(ROOT, "logs", "combined.log");

module.exports = {
  apps: [
    {
      name: "aiquanttrader-mt5",
      cwd: ROOT,
      script: "scripts/start_mt5.sh",
      interpreter: "bash",
      autorestart: true,
      restart_delay: 10000,
      max_restarts: 20,
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
      time: true,
    },
    {
      name: "aiquanttrader-review",
      cwd: ROOT,
      script: "scripts/autonomous_review_loop.py",
      interpreter: path.join(ROOT, ".venv", "bin", "python"),
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 20,
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
      time: true,
    },
    {
      name: "aiquanttrader-watchdog",
      cwd: ROOT,
      script: "scripts/mt5_watchdog.py",
      interpreter: path.join(ROOT, ".venv", "bin", "python"),
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 20,
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
      time: true,
    },
    {
      name: "aiquanttrader-dashboard",
      cwd: ROOT,
      script: path.join(ROOT, ".venv", "bin", "streamlit"),
      args: "run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false --server.enableCORS false --server.enableXsrfProtection false",
      interpreter: "none",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 50,
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
      time: true,
    },
  ],
};
