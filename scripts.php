<?php
require __DIR__ . '/includes/scripts_init.php';

$pdo  = scripts_db();
$rows = $pdo->query(
    'SELECT slug, title, description, runs_count, updated_at
       FROM scripts
      WHERE is_public = 1
      ORDER BY title COLLATE NOCASE'
)->fetchAll();

$base = scripts_base_url();
?><!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Скрипты развёртывания</title>
  <link rel="stylesheet" type="text/css" href="/media/assets/bootstrap-grid-only/css/grid12.css">
  <link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700" rel="stylesheet">
  <link rel="stylesheet" type="text/css" href="/media/css/style.css">
  <style>
    .script-card  { border:1px solid #e3e3e3; border-radius:6px; padding:16px; margin-bottom:14px; background:#fff; }
    .script-card h4 { margin:0 0 6px 0; }
    .script-card .meta { color:#888; font-size:13px; margin-bottom:8px; }
    .install-cmd { background:#1e1e1e; color:#e0e0e0; padding:10px 12px; border-radius:4px;
                   font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
                   font-size:13px; overflow-x:auto; position:relative; white-space:pre; }
    .install-cmd .copy-btn { position:absolute; right:6px; top:6px; padding:2px 10px; cursor:pointer;
                             background:#444; color:#fff; border:0; border-radius:3px; font-size:12px; }
    .install-cmd .copy-btn:hover { background:#666; }
    .admin-link { float:right; font-size:13px; }
  </style>
</head>
<body>
  <div id="wrapper">
    <?php if (file_exists(__DIR__ . '/includes/headers.php')) include __DIR__ . '/includes/headers.php'; ?>

    <div id="content">
      <div class="container">
        <div class="row">
          <section class="content__left col-md-8">
            <div class="block">
              <a class="admin-link" href="/script_admin.php">админка →</a>
              <h3>Скрипты для быстрой настройки серверов</h3>
              <div class="block__content">
                <p>На любом сервере выполни одну из команд ниже — нода поднимется автоматически.</p>

                <?php if (!$rows): ?>
                  <p>Пока ни одного скрипта. <a href="/script_admin.php">Добавь первый в админке.</a></p>
                <?php endif; ?>

                <?php foreach ($rows as $r):
                    $url = $base . '/s.php?slug=' . urlencode($r['slug']);
                    $cmd = 'bash <(curl -sSL ' . $url . ')';
                ?>
                  <div class="script-card">
                    <h4><?= h($r['title']) ?></h4>
                    <div class="meta">
                      slug: <code><?= h($r['slug']) ?></code>
                      · запусков: <?= (int)$r['runs_count'] ?>
                      · обновлён: <?= h($r['updated_at']) ?>
                    </div>
                    <?php if ($r['description'] !== ''): ?>
                      <p><?= nl2br(h($r['description'])) ?></p>
                    <?php endif; ?>
                    <div class="install-cmd"><button type="button" class="copy-btn"
                      onclick="navigator.clipboard.writeText(this.nextSibling.textContent); this.textContent='скопировано'; setTimeout(()=>this.textContent='copy', 1500);"
                    >copy</button><code><?= h($cmd) ?></code></div>
                  </div>
                <?php endforeach; ?>

              </div>
            </div>
          </section>
          <section class="content__right col-md-4">
            <?php if (file_exists(__DIR__ . '/includes/side_bar.php')) include __DIR__ . '/includes/side_bar.php'; ?>
          </section>
        </div>
      </div>
    </div>

    <?php if (file_exists(__DIR__ . '/includes/futer.php')) include __DIR__ . '/includes/futer.php'; ?>
  </div>
</body>
</html>
