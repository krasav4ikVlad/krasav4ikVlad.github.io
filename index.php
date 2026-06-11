<?php
// Главная страница портала nodewiki.info — карточки на доступные сервисы и поддомены.
// Чистый PHP без внешних зависимостей и БД, чтобы страница рендерилась даже на
// свежей машине без настройки.

function nw_h(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

// Сюда добавляй новые сервисы по мере появления поддоменов / страниц.
$tiles = [
    [
        'title' => 'Scripts',
        'url'   => 'https://scripts.nodewiki.info/',
        'desc'  => 'Панель быстрого разворачивания: пишешь bash-скрипт, получаешь URL — на любом сервере '
                 . 'запускаешь одной командой <code>bash &lt;(curl -sSL …)</code>.',
        'tag'   => 'панель',
    ],
    [
        'title' => 'VPN ping',
        'url'   => 'https://github.com/krasav4ikVlad/krasav4ikVlad.github.io/blob/master/vpn_ping.py',
        'desc'  => 'CLI-утилита: парсит подписку VPN, проверяет TCP/ICMP до каждого сервера, '
                 . 'считает % работоспособности по группам стран.',
        'tag'   => 'python',
    ],
    // [
    //     'title' => 'VPN status',
    //     'url'   => 'https://status.nodewiki.info/',
    //     'desc'  => 'Публичный статус-пейдж для VPN-подписки (in progress).',
    //     'tag'   => 'скоро',
    // ],
];
?><!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NodeWiki — инструменты для админов VPN</title>
  <link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700" rel="stylesheet">
  <style>
    * { box-sizing: border-box; }
    body { margin:0; font-family: Roboto, -apple-system, "Segoe UI", sans-serif;
           background:#f6f6f7; color:#222; line-height:1.5; }
    a { color:#226622; text-decoration:none; }
    a:hover { text-decoration:underline; }
    code { background:#eee; padding:1px 5px; border-radius:3px; font-size:.92em; }

    .hero { max-width:900px; margin:0 auto; padding:60px 24px 24px; text-align:center; }
    .hero h1 { font-size:42px; font-weight:700; margin:0 0 12px; letter-spacing:-0.5px; }
    .hero h1 .accent { color:#226622; }
    .hero p { font-size:17px; color:#555; max-width:600px; margin:0 auto 8px; }

    .grid { max-width:900px; margin:0 auto; padding:30px 24px 60px;
            display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:16px; }
    .tile { display:block; background:#fff; border:1px solid #e3e3e3; border-radius:8px;
            padding:22px 22px 20px; transition: border-color .15s, transform .15s, box-shadow .15s;
            color:inherit; }
    .tile:hover { border-color:#226622; text-decoration:none;
                  transform:translateY(-1px); box-shadow:0 6px 20px rgba(0,0,0,.06); }
    .tile h3 { margin:0 0 8px; font-size:20px; display:flex; align-items:center; justify-content:space-between; }
    .tile .tag { font-size:11px; background:#eef5ee; color:#226622; padding:3px 8px; border-radius:10px;
                 font-weight:500; text-transform:uppercase; letter-spacing:.5px; }
    .tile p { margin:0; color:#555; font-size:14px; }
    .tile .url { display:block; margin-top:12px; color:#888; font-size:13px;
                 font-family: ui-monospace, Menlo, Consolas, monospace; word-break:break-all; }

    footer { max-width:900px; margin:0 auto; padding:20px 24px 40px; color:#999;
             font-size:13px; text-align:center; border-top:1px solid #eaeaea; }
    footer a { color:#888; }
  </style>
</head>
<body>
  <section class="hero">
    <h1>Node<span class="accent">Wiki</span></h1>
    <p>Инструменты и панели для админов VPN-сервисов: быстрая настройка серверов, мониторинг подписок, обход блокировок.</p>
  </section>

  <section class="grid">
    <?php foreach ($tiles as $t): ?>
      <a class="tile" href="<?= nw_h($t['url']) ?>">
        <h3><?= nw_h($t['title']) ?> <span class="tag"><?= nw_h($t['tag']) ?></span></h3>
        <p><?= $t['desc'] /* допускаем безопасный inline-HTML в desc */ ?></p>
        <span class="url"><?= nw_h(preg_replace('#^https?://#', '', $t['url'])) ?></span>
      </a>
    <?php endforeach; ?>
  </section>

  <footer>
    nodewiki.info · <a href="https://github.com/krasav4ikVlad/krasav4ikVlad.github.io">GitHub</a>
  </footer>
</body>
</html>
