<?php
// Админка для управления bash-скриптами развёртывания.
// При первом заходе попросит установить пароль (сохранит хэш в includes/scripts_config.php).
// Дальше — стандартный логин + CRUD.

require __DIR__ . '/includes/scripts_init.php';
scripts_session_start();

$pdo    = scripts_db();
$action = $_GET['action'] ?? '';
$msg    = '';      // зелёная плашка
$error  = '';      // красная плашка

// --- первый запуск: установка пароля ---------------------------------------
if (scripts_admin_pass_hash() === null) {
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['setup_password'])) {
        $p1 = (string)($_POST['p1'] ?? '');
        $p2 = (string)($_POST['p2'] ?? '');
        if (strlen($p1) < 8) {
            $error = 'Минимум 8 символов.';
        } elseif ($p1 !== $p2) {
            $error = 'Пароли не совпадают.';
        } else {
            scripts_set_admin_password($p1);
            $_SESSION['scripts_admin'] = true;
            header('Location: /script_admin.php');
            exit;
        }
    }
    render_layout('Установка пароля', function () use ($error) { ?>
        <h3>Первый запуск — задай пароль администратора</h3>
        <p class="meta">Хэш сохранится в <code>includes/scripts_config.php</code>.</p>
        <?php if ($error): ?><div class="err"><?= h($error) ?></div><?php endif; ?>
        <form method="POST" class="form">
          <div class="form__group"><input type="password" name="p1" class="form__control" placeholder="Новый пароль (≥ 8 символов)" required></div>
          <div class="form__group"><input type="password" name="p2" class="form__control" placeholder="Повтори пароль" required></div>
          <div class="form__group"><input type="submit" name="setup_password" class="form__control" value="Установить пароль"></div>
        </form>
    <?php });
    exit;
}

// --- логин / логаут --------------------------------------------------------
if ($action === 'logout') {
    $_SESSION = [];
    session_destroy();
    header('Location: /script_admin.php');
    exit;
}

if (!scripts_is_logged_in()) {
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['login'])) {
        $pass = (string)($_POST['password'] ?? '');
        if (password_verify($pass, scripts_admin_pass_hash() ?? '')) {
            session_regenerate_id(true);
            $_SESSION['scripts_admin'] = true;
            header('Location: /script_admin.php');
            exit;
        }
        usleep(700_000);
        $error = 'Неверный пароль.';
    }
    render_layout('Вход', function () use ($error) { ?>
        <h3>Вход в админку скриптов</h3>
        <?php if ($error): ?><div class="err"><?= h($error) ?></div><?php endif; ?>
        <form method="POST" class="form">
          <div class="form__group"><input type="password" name="password" class="form__control" placeholder="Пароль" autofocus required></div>
          <div class="form__group"><input type="submit" name="login" class="form__control" value="Войти"></div>
        </form>
    <?php });
    exit;
}

// --- POST: сохранение / удаление -------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    scripts_csrf_check();

    if (isset($_POST['save'])) {
        $id          = (int)($_POST['id'] ?? 0);
        $title       = trim((string)($_POST['title'] ?? ''));
        $slug_input  = trim((string)($_POST['slug'] ?? ''));
        $description = trim((string)($_POST['description'] ?? ''));
        $content     = (string)($_POST['content'] ?? '');
        $is_public   = isset($_POST['is_public']) ? 1 : 0;

        $slug = $slug_input !== '' ? scripts_slugify($slug_input) : scripts_slugify($title);

        if ($title === '')                          $error = 'Введи название.';
        elseif (!preg_match(SCRIPTS_SLUG_RE, $slug)) $error = 'Некорректный slug (только a-z, 0-9, -).';
        elseif (trim($content) === '')              $error = 'Скрипт пустой.';

        if (!$error) {
            try {
                if ($id > 0) {
                    $stmt = $pdo->prepare(
                        "UPDATE scripts SET slug=?, title=?, description=?, content=?, is_public=?,
                                updated_at=datetime('now') WHERE id=?"
                    );
                    $stmt->execute([$slug, $title, $description, $content, $is_public, $id]);
                    $msg = 'Скрипт обновлён.';
                } else {
                    $stmt = $pdo->prepare(
                        "INSERT INTO scripts (slug, title, description, content, is_public)
                              VALUES (?, ?, ?, ?, ?)"
                    );
                    $stmt->execute([$slug, $title, $description, $content, $is_public]);
                    $msg = 'Скрипт создан.';
                    $id  = (int)$pdo->lastInsertId();
                }
                header('Location: /script_admin.php?msg=' . urlencode($msg));
                exit;
            } catch (PDOException $e) {
                if (str_contains($e->getMessage(), 'UNIQUE')) {
                    $error = 'Slug уже занят — выбери другой.';
                } else {
                    $error = 'Ошибка БД: ' . $e->getMessage();
                }
            }
        }
        // если ошибка — провалится в редактор ниже, заполнив поля из POST
        $edit_row = compact('id', 'slug', 'title', 'description', 'content', 'is_public');
    }

    if (isset($_POST['delete'])) {
        $id = (int)($_POST['id'] ?? 0);
        $pdo->prepare('DELETE FROM scripts WHERE id = ?')->execute([$id]);
        header('Location: /script_admin.php?msg=' . urlencode('Скрипт удалён.'));
        exit;
    }
}

if (isset($_GET['msg'])) $msg = (string)$_GET['msg'];

// --- роутинг страниц -------------------------------------------------------
if ($action === 'add' || $action === 'edit') {
    if ($action === 'edit') {
        $id   = (int)($_GET['id'] ?? 0);
        $stmt = $pdo->prepare('SELECT * FROM scripts WHERE id = ?');
        $stmt->execute([$id]);
        $row = $stmt->fetch() ?: null;
        if (!$row) {
            header('Location: /script_admin.php');
            exit;
        }
    } else {
        $row = ['id' => 0, 'slug' => '', 'title' => '', 'description' => '',
                'content' => "#!/usr/bin/env bash\nset -euo pipefail\n\n# твой скрипт здесь\n", 'is_public' => 1];
    }
    // если был POST с ошибкой — переопределяем строку значениями из формы
    if (!empty($edit_row)) $row = $edit_row + $row;

    render_layout($action === 'edit' ? 'Редактирование скрипта' : 'Новый скрипт',
        function () use ($row, $msg, $error, $action) { ?>
        <h3><?= $action === 'edit' ? 'Редактировать скрипт' : 'Новый скрипт' ?></h3>
        <?php if ($msg):   ?><div class="ok"><?= h($msg) ?></div><?php endif; ?>
        <?php if ($error): ?><div class="err"><?= h($error) ?></div><?php endif; ?>
        <form method="POST" class="form">
          <input type="hidden" name="csrf" value="<?= h(scripts_csrf_token()) ?>">
          <input type="hidden" name="id"   value="<?= (int)$row['id'] ?>">
          <div class="form__group">
            <label>Название</label>
            <input type="text" name="title" class="form__control" required
                   value="<?= h((string)$row['title']) ?>" placeholder="Marzban install">
          </div>
          <div class="form__group">
            <label>Slug (URL-имя). Пусто → сгенерится из названия.</label>
            <input type="text" name="slug" class="form__control"
                   value="<?= h((string)$row['slug']) ?>" placeholder="marzban">
          </div>
          <div class="form__group">
            <label>Описание</label>
            <textarea name="description" class="form__control" rows="3"
                      placeholder="Что делает скрипт"><?= h((string)$row['description']) ?></textarea>
          </div>
          <div class="form__group">
            <label>Bash-скрипт</label>
            <textarea name="content" class="form__control script-content" rows="22" required
                      spellcheck="false"><?= h((string)$row['content']) ?></textarea>
          </div>
          <div class="form__group">
            <label><input type="checkbox" name="is_public" value="1" <?= !empty($row['is_public']) ? 'checked' : '' ?>>
              публичный (доступен через /s.php)</label>
          </div>
          <div class="form__group">
            <input type="submit" name="save" class="form__control" value="Сохранить">
          </div>
        </form>
    <?php });
    exit;
}

// --- дефолт: список скриптов ----------------------------------------------
$rows = $pdo->query('SELECT * FROM scripts ORDER BY updated_at DESC')->fetchAll();
$base = scripts_base_url();

render_layout('Скрипты — админка', function () use ($rows, $base, $msg, $error) { ?>
    <h3>Скрипты <a class="btn-add" href="/script_admin.php?action=add">+ новый</a>
        <a class="btn-logout" href="/script_admin.php?action=logout">выйти</a></h3>
    <?php if ($msg):   ?><div class="ok"><?= h($msg) ?></div><?php endif; ?>
    <?php if ($error): ?><div class="err"><?= h($error) ?></div><?php endif; ?>

    <?php if (!$rows): ?>
      <p>Ни одного скрипта. <a href="/script_admin.php?action=add">Создай первый.</a></p>
    <?php endif; ?>

    <table class="scripts-table">
      <thead><tr>
        <th>Название</th><th>Slug</th><th>Публ.</th><th>Запуски</th>
        <th>Обновлён</th><th>Команда</th><th></th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r):
          $url = $base . '/s.php?slug=' . urlencode($r['slug']);
          $cmd = 'bash <(curl -sSL ' . $url . ')'; ?>
        <tr>
          <td><a href="/script_admin.php?action=edit&id=<?= (int)$r['id'] ?>"><?= h($r['title']) ?></a></td>
          <td><code><?= h($r['slug']) ?></code></td>
          <td><?= $r['is_public'] ? '✓' : '—' ?></td>
          <td><?= (int)$r['runs_count'] ?></td>
          <td><?= h($r['updated_at']) ?></td>
          <td><code class="cmd-small" title="<?= h($cmd) ?>"><?= h($cmd) ?></code></td>
          <td>
            <form method="POST" style="display:inline" onsubmit="return confirm('Удалить «<?= h($r['title']) ?>»?')">
              <input type="hidden" name="csrf" value="<?= h(scripts_csrf_token()) ?>">
              <input type="hidden" name="id"   value="<?= (int)$r['id'] ?>">
              <button type="submit" name="delete" class="btn-del">×</button>
            </form>
          </td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
<?php });


// ---------------------------------------------------------------------------
// Общий layout для всех экранов админки.
function render_layout(string $title, callable $body): void { ?>
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title><?= h($title) ?> — Админка скриптов</title>
  <link rel="stylesheet" type="text/css" href="/media/assets/bootstrap-grid-only/css/grid12.css">
  <link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700" rel="stylesheet">
  <link rel="stylesheet" type="text/css" href="/media/css/style.css">
  <style>
    .block { padding: 18px; }
    .meta  { color:#888; font-size:13px; }
    .ok    { background:#e6f7e6; color:#226622; padding:8px 12px; border-radius:4px; margin-bottom:12px; }
    .err   { background:#fdecec; color:#992222; padding:8px 12px; border-radius:4px; margin-bottom:12px; }
    .form  label { display:block; font-size:13px; color:#555; margin-bottom:4px; }
    .form__group { margin-bottom:12px; }
    .script-content { font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
                      font-size:13px; background:#1e1e1e; color:#e0e0e0; }
    .scripts-table { width:100%; border-collapse:collapse; font-size:14px; }
    .scripts-table th, .scripts-table td { padding:8px 10px; border-bottom:1px solid #eee; text-align:left; vertical-align:top; }
    .scripts-table th { background:#fafafa; }
    .cmd-small { display:inline-block; max-width:380px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:middle; }
    .btn-add    { float:right; font-size:14px; margin-left:10px; background:#226622; color:#fff; padding:4px 10px; border-radius:3px; text-decoration:none; }
    .btn-logout { float:right; font-size:13px; color:#888; text-decoration:none; padding:4px 10px; }
    .btn-del    { background:#cc3333; color:#fff; border:0; border-radius:3px; padding:2px 8px; cursor:pointer; }
  </style>
</head>
<body>
  <div id="wrapper">
    <?php if (file_exists(__DIR__ . '/includes/headers.php')) include __DIR__ . '/includes/headers.php'; ?>
    <div id="content">
      <div class="container">
        <div class="row">
          <section class="content__left col-md-12">
            <div class="block">
              <div class="block__content">
                <?php $body(); ?>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
    <?php if (file_exists(__DIR__ . '/includes/futer.php')) include __DIR__ . '/includes/futer.php'; ?>
  </div>
</body>
</html><?php }
