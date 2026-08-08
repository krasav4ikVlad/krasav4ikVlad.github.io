// pm2: два процесса вместо одного.
//
//   rsvpn-bot  — сам бот (polling) и планировщик списаний
//   rsvpn-api  — вебхуки платёжек и панели, uvicorn
//
// Запускать так:  pm2 start ecosystem.config.js
//
// Про interpreter: 'none'. pm2 по умолчанию запускает файл через node.
// Здесь в script уже стоит python из виртуального окружения, поэтому
// подставлять что-то ещё не нужно — иначе pm2 попробует «выполнить»
// интерпретатор интерпретатором.
//
// Про cwd. Бот читает .env рядом с pyproject.toml, а картинки — из media/
// относительно рабочей папки. Запуск из другого каталога = «Не задана
// переменная окружения API_TOKEN» и экраны без картинок.
//
// Про env. Значения отсюда перекрывают .env: файл кладёт переменные через
// setdefault, то есть настоящее окружение всегда сильнее. Поэтому опасные
// переключатели (планировщик) стоят здесь — их видно в одном месте.

const ROOT = __dirname;
const PYTHON = `${ROOT}/.venv/bin/python`;

module.exports = {
  apps: [
    {
      name: 'rsvpn-bot',
      cwd: ROOT,
      script: PYTHON,
      args: '-m app.main_bot',
      interpreter: 'none',

      // Один процесс на бота. Два polling-процесса с одним токеном Telegram
      // обслуживает по очереди: половина нажатий уходит в никуда.
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: '30s',
      restart_delay: 5000,
      max_memory_restart: '500M',

      env: {
        PYTHONUNBUFFERED: '1',   // без этого логи висят в буфере и pm2 logs пуст
        // 0, пока старый бот ещё жив: два планировщика на одной базе
        // спишут деньги дважды.
        SCHEDULER_ENABLED: '1',
      },

      time: true,                // метка времени в логах pm2
      error_file: `${ROOT}/logs/bot.error.log`,
      out_file: `${ROOT}/logs/bot.out.log`,
    },
    {
      name: 'rsvpn-api',
      cwd: ROOT,
      script: `${ROOT}/.venv/bin/uvicorn`,
      args: 'app.main_api:app --host 127.0.0.1 --port 8000',
      interpreter: 'none',

      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: '30s',
      restart_delay: 5000,

      env: {
        PYTHONUNBUFFERED: '1',
        // Планировщик живёт только в процессе бота, здесь переменная ни на
        // что не влияет — но пусть стоит явно, чтобы не гадать при чтении.
        SCHEDULER_ENABLED: '0',
      },

      time: true,
      error_file: `${ROOT}/logs/api.error.log`,
      out_file: `${ROOT}/logs/api.out.log`,
    },
  ],
};
