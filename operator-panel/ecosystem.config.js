// PM2: pm2 start ecosystem.config.js
module.exports = {
  apps: [
    {
      name: 'operator-panel',
      cwd: __dirname,
      script: 'venv/bin/uvicorn',
      args: 'app.main:app --host 127.0.0.1 --port 8100',
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      env: {
        // секреты держим в .env, не здесь
      },
    },
  ],
};
