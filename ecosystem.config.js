// PM2 ecosystem for FinRobot.
// Active trading is MT5 demo via FinRobotBridgeEA on XAUUSD and BTCUSD.
// Hyperliquid paper trading is intentionally disabled/retired.

module.exports = {
  apps: [
    {
      name: "mt5-terminal",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/start_mt5.sh",
      interpreter: "bash",
      autorestart: true,
      restart_delay: 10000,
      max_restarts: 20,
      out_file: "/home/openclaw/FinRobot/logs/pm2_mt5.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_mt5.err.log",
      time: true,
    },
    {
      name: "autonomous-review",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/autonomous_review_loop.py",
      interpreter: "/home/openclaw/FinRobot/.venv/bin/python",
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 20,
      out_file: "/home/openclaw/FinRobot/logs/pm2_autonomous_review.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_autonomous_review.err.log",
      time: true,
    },
    {
      name: "moonshot-dashboard",
      cwd: "/home/openclaw/FinRobot",
      script: "/home/openclaw/FinRobot/.venv/bin/streamlit",
      args: "run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false --server.enableCORS false --server.enableXsrfProtection false",
      interpreter: "none",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 50,
      out_file: "/home/openclaw/FinRobot/logs/pm2_dashboard.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_dashboard.err.log",
      time: true,
    },
    {
      name: "log-aggregator",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/log_aggregator.sh",
      interpreter: "bash",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 50,
      out_file: "/home/openclaw/FinRobot/logs/pm2_aggregator.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_aggregator.err.log",
      time: true,
    },
  ],
};
