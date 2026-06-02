// PM2 ecosystem for FinRobot.
// Active trading is MT5 demo via FinRobotBridgeEA on XAUUSD and BTCUSD.
// All PM2 service output is intentionally consolidated into logs/combined.log.

const COMBINED_LOG = "/home/openclaw/FinRobot/logs/combined.log";

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
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
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
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
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
      out_file: COMBINED_LOG,
      error_file: COMBINED_LOG,
      merge_logs: true,
      env: {
        DASHBOARD_AUTO_REFRESH: "true",
        DASHBOARD_REFRESH_SECONDS: "10",
      },
      time: true,
    },
  ],
};
