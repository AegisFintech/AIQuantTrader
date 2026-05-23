// pm2 ecosystem for FinRobot Moonshot.
// Usage:
//   pm2 start ecosystem.config.js
//   pm2 logs
//   pm2 save                  # persist process list
//   pm2 startup                # generate boot script (one-time)
//
// Both processes load secrets from ~/FinRobot/.env via python-dotenv,
// so no secrets are hard-coded here.

module.exports = {
  apps: [
    {
      name: "moonshot-daemon",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/run_daemon.py",
      interpreter: "/home/openclaw/FinRobot/.venv/bin/python",
      args: "--interval 30 --balance 500000",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 50,
      out_file: "/home/openclaw/FinRobot/logs/pm2_daemon.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_daemon.err.log",
      time: true,
    },
    {
      name: "moonshot-improver",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/run_improver.py",
      interpreter: "/home/openclaw/FinRobot/.venv/bin/python",
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 50,
      out_file: "/home/openclaw/FinRobot/logs/pm2_improver.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_improver.err.log",
      time: true,
    },

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
  ],
};
