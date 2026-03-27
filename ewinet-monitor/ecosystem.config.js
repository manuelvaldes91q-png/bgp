module.exports = {
  apps: [
    {
      name: "ewinet-monitor",
      script: "main.py",
      interpreter: "python3",
      args: "",
      cwd: "/opt/ewinet-monitor",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "512M",
      restart_delay: 5000,
      max_restarts: 50,
      min_uptime: "10s",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONDONTWRITEBYTECODE: "1",
      },
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "logs/pm2-error.log",
      out_file: "logs/pm2-out.log",
      merge_logs: true,
      kill_timeout: 30000,
      listen_timeout: 10000,
    },
  ],
};
