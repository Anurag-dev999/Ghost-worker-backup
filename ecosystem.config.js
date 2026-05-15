module.exports = {
  apps: [
    {
      name        : "ghost-dashboard",
      script      : "/home/ubuntu/ghost_worker/ghostenv/bin/gunicorn",
      args: "-w 1 -b 0.0.0.0:5000 --timeout 120 dashboard_tracker:app",
      cwd         : "/home/ubuntu/ghost_worker",
      interpreter : "none",
      autorestart : true,
      max_restarts: 10,
      watch       : false,
      env         : {
        PATH : "/home/ubuntu/ghost_worker/ghostenv/bin:/usr/bin:/bin"
      },
      error_file  : "/home/ubuntu/ghost_worker/logs/dashboard-error.log",
      out_file    : "/home/ubuntu/ghost_worker/logs/dashboard-out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss"
    },
    {
      name        : "ghost-scheduler",
      script      : "/home/ubuntu/ghost_worker/ghostenv/bin/python3",
      args        : "scheduler.py",
      cwd         : "/home/ubuntu/ghost_worker",
      interpreter : "none",
      autorestart : true,
      max_restarts: 10,
      watch       : false,
      env         : {
        PATH : "/home/ubuntu/ghost_worker/ghostenv/bin:/usr/bin:/bin"
      },
      error_file  : "/home/ubuntu/ghost_worker/logs/scheduler-error.log",
      out_file    : "/home/ubuntu/ghost_worker/logs/scheduler-out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss"
    }
  ]
}
