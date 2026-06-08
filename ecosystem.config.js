// PM2 ecosystem for FinRobot.
// Active trading is MT5 demo via FinRobotBridgeEA on XAUUSD and BTCUSD.
// All PM2 service output is intentionally consolidated into logs/combined.log.

const path = require("path");

const ROOT = __dirname;
const COMBINED_LOG = path.join(ROOT, "logs", "combined.log");

module.exports = {
  apps: [
    {
      name: "mt5-terminal",
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
      name: "autonomous-review",
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
  ],
};
