<?php
// Single-page админка bash-скриптов:
//   - список карточек со ссылками на /s.php?slug=…
//   - "+" открывает модалку с пустым редактором
//   - "Редактировать" открывает ту же модалку, предзаполненную
//   - после сохранения — баннер с готовой командой и кнопкой Копировать

require __DIR__ . '/includes/scripts_init.php';
scripts_session_start();

$pdo   = scripts_db();
$msg   = '';
$error = '';

// --- первый запуск: установка пароля ---------------------------------------
if (scripts_admin_pass_hash() === null) {
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['setup_password'])) {
        $p1 = (string)($_POST['p1'] ?? '');
        $p2 = (string)($_POST['p2'] ?? '');
        if (strlen($p1) < 8)      $error = 'Минимум 8 символов.';
        elseif ($p1 !== $p2)      $error = 'Пароли не совпадают.';
        else {
            scripts_set_admin_password($p1);
            $_SESSION['scripts_admin'] = true;
            header('Location: /script_admin.php');
            exit;
        }
    }
    render_layout('Установка пароля', function () use ($error) { ?>
        <h3>Первый запуск — задай пароль администратора</h3>
        <p class="meta">Хэш сохранится в <code>includes/scripts_config.php</code> и в git не попадёт.</p>
        <?php if ($error): ?><div class="err"><?= h($error) ?></div><?php endif; ?>
        <form method="POST" class="form" autocomplete="off">
          <div class="form__group"><input type="password" name="p1" class="form__control" placeholder="Новый пароль (≥ 8 символов)" required></div>
          <div class="form__group"><input type="password" name="p2" class="form__control" placeholder="Повтори пароль" required></div>
          <div class="form__group"><input type="submit" name="setup_password" class="form__control btn-primary" value="Установить пароль"></div>
        </form>
    <?php });
    exit;
}

// --- логин / логаут --------------------------------------------------------
if (($_GET['action'] ?? '') === 'logout') {
    $_SESSION = [];
    session_destroy();
    header('Location: /script_admin.php');
    exit;
}

if (!scripts_is_logged_in()) {
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['login'])) {
        if (password_verify((string)($_POST['password'] ?? ''), scripts_admin_pass_hash() ?? '')) {
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
        <form method="POST" class="form" autocomplete="off">
          <div class="form__group"><input type="password" name="password" class="form__control" placeholder="Пароль" autofocus required></div>
          <div class="form__group"><input type="submit" name="login" class="form__control btn-primary" value="Войти"></div>
        </form>
    <?php });
    exit;
}

// --- POST: сохранение / удаление -------------------------------------------
$open_modal   = false;       // открыть редактор на загрузке страницы
$form_data    = null;        // данные для предзаполнения редактора при ошибке

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

        if ($title === '')                           $error = 'Введи название.';
        elseif (!preg_match(SCRIPTS_SLUG_RE, $slug)) $error = 'Slug может содержать только a-z, 0-9 и дефис.';
        elseif (trim($content) === '')               $error = 'Скрипт пустой.';

        if (!$error) {
            try {
                if ($id > 0) {
                    $pdo->prepare(
                        "UPDATE scripts SET slug=?, title=?, description=?, content=?, is_public=?,
                                updated_at=datetime('now') WHERE id=?"
                    )->execute([$slug, $title, $description, $content, $is_public, $id]);
                } else {
                    $pdo->prepare(
                        "INSERT INTO scripts (slug, title, description, content, is_public)
                              VALUES (?, ?, ?, ?, ?)"
                    )->execute([$slug, $title, $description, $content, $is_public]);
                    $id = (int)$pdo->lastInsertId();
                }
                header('Location: /script_admin.php?saved=' . $id);
                exit;
            } catch (PDOException $e) {
                $error = str_contains($e->getMessage(), 'UNIQUE')
                    ? 'Slug уже занят — выбери другой.'
                    : 'Ошибка БД: ' . $e->getMessage();
            }
        }
        // ошибка — переоткрываем модалку с тем что ввели
        $open_modal = true;
        $form_data  = compact('id', 'slug', 'title', 'description', 'content', 'is_public');
    }

    if (isset($_POST['delete'])) {
        $id = (int)($_POST['id'] ?? 0);
        $pdo->prepare('DELETE FROM scripts WHERE id = ?')->execute([$id]);
        header('Location: /script_admin.php?msg=' . urlencode('Скрипт удалён.'));
        exit;
    }
}

// --- GET-параметры → плашки и автооткрытие модалки -------------------------
$saved_id = (int)($_GET['saved'] ?? 0);
if ($saved_id)             $msg = 'Скрипт сохранён.';
if (isset($_GET['msg']))   $msg = (string)$_GET['msg'];
if (($_GET['action'] ?? '') === 'add') {
    $open_modal = true;
    $form_data  = null;     // пустая форма
}
if (($_GET['action'] ?? '') === 'edit') {
    $eid  = (int)($_GET['id'] ?? 0);
    $stmt = $pdo->prepare('SELECT id, slug, title, description, content, is_public FROM scripts WHERE id = ?');
    $stmt->execute([$eid]);
    if ($row = $stmt->fetch()) {
        $open_modal = true;
        $form_data  = $row;
    }
}

// --- рендер списка ---------------------------------------------------------
$rows = $pdo->query(
    'SELECT id, slug, title, description, content, is_public, runs_count, updated_at
       FROM scripts ORDER BY updated_at DESC'
)->fetchAll();
$base = scripts_base_url();

// данные для JS — словарь {id: {…}} с полной информацией для предзаполнения формы
$scripts_json = json_encode(
    array_column($rows, null, 'id'),
    JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT
);

$saved_row = null;
if ($saved_id) {
    foreach ($rows as $r) {
        if ((int)$r['id'] === $saved_id) { $saved_row = $r; break; }
    }
}

render_layout('Скрипты', function () use ($rows, $base, $msg, $error, $open_modal,
                                          $form_data, $saved_row, $scripts_json) {
    $default_content = "#!/usr/bin/env bash\nset -euo pipefail\n\n# твой скрипт здесь\n";
    ?>
    <div class="admin-header">
      <h3>Скрипты <span class="meta-count"><?= count($rows) ?></span></h3>
      <div class="admin-actions">
        <button type="button" class="btn-add js-new-script" title="Новый скрипт">+ Новый скрипт</button>
        <a class="btn-logout" href="/script_admin.php?action=logout">выйти</a>
      </div>
    </div>

    <?php if ($error): ?><div class="err"><?= h($error) ?></div><?php endif; ?>

    <?php if ($saved_row):
        $url = $base . '/s.php?slug=' . urlencode($saved_row['slug']);
        $cmd = 'bash <(curl -sSL ' . $url . ')'; ?>
      <div class="ok saved-banner">
        <strong>«<?= h($saved_row['title']) ?>» сохранён.</strong> Команда для запуска:
        <div class="install-cmd"><button type="button" class="copy-btn js-copy" data-copy="<?= h($cmd) ?>">копировать</button><code><?= h($cmd) ?></code></div>
      </div>
    <?php elseif ($msg): ?>
      <div class="ok"><?= h($msg) ?></div>
    <?php endif; ?>

    <?php if (!$rows): ?>
      <p class="empty">Пока ни одного скрипта. Нажми <b>+ Новый скрипт</b>, чтобы создать первый.</p>
    <?php endif; ?>

    <div class="cards">
      <?php foreach ($rows as $r):
        $url = $base . '/s.php?slug=' . urlencode($r['slug']);
        $cmd = 'bash <(curl -sSL ' . $url . ')';
        $highlight = $saved_row && (int)$saved_row['id'] === (int)$r['id'];
        ?>
        <div class="card<?= $highlight ? ' highlight' : '' ?>" id="script-<?= (int)$r['id'] ?>">
          <div class="card__head">
            <div class="card__title">
              <h4><?= h($r['title']) ?> <?= $r['is_public'] ? '' : '<span class="badge-private">приватный</span>' ?></h4>
              <div class="meta">
                <code><?= h($r['slug']) ?></code> · запусков: <?= (int)$r['runs_count'] ?>
                · обновлён: <?= h($r['updated_at']) ?>
              </div>
            </div>
            <div class="card__actions">
              <button type="button" class="btn-edit js-edit-script" data-id="<?= (int)$r['id'] ?>">редактировать</button>
              <form method="POST" style="display:inline" onsubmit="return confirm('Удалить «<?= h($r['title']) ?>»?')">
                <input type="hidden" name="csrf" value="<?= h(scripts_csrf_token()) ?>">
                <input type="hidden" name="id"   value="<?= (int)$r['id'] ?>">
                <button type="submit" name="delete" class="btn-del" title="Удалить">×</button>
              </form>
            </div>
          </div>
          <?php if ($r['description'] !== ''): ?>
            <p class="card__desc"><?= nl2br(h($r['description'])) ?></p>
          <?php endif; ?>
          <div class="install-cmd"><button type="button" class="copy-btn js-copy" data-copy="<?= h($cmd) ?>">копировать</button><code><?= h($cmd) ?></code></div>
        </div>
      <?php endforeach; ?>
    </div>

    <!-- ===================== Модалка-редактор ===================== -->
    <dialog id="editor" class="editor"<?= $open_modal ? ' open' : '' ?>>
      <form method="POST" class="form" id="editor-form">
        <div class="editor__head">
          <h3 id="editor-title"><?php
            $is_edit = !empty($form_data['id']);
            echo $is_edit
              ? 'Редактирование: ' . h((string)$form_data['title'])
              : 'Новый скрипт';
          ?></h3>
          <button type="button" class="btn-close js-cancel" aria-label="Закрыть">×</button>
        </div>

        <input type="hidden" name="csrf" value="<?= h(scripts_csrf_token()) ?>">
        <input type="hidden" name="id"   id="f-id"   value="<?= (int)($form_data['id'] ?? 0) ?>">

        <div class="form__row">
          <div class="form__group form__group--grow">
            <label for="f-title">Название</label>
            <input type="text" id="f-title" name="title" class="form__control" required
                   value="<?= h((string)($form_data['title'] ?? '')) ?>" placeholder="Marzban install">
          </div>
          <div class="form__group">
            <label for="f-slug">Slug (пусто → из названия)</label>
            <input type="text" id="f-slug" name="slug" class="form__control"
                   value="<?= h((string)($form_data['slug'] ?? '')) ?>" placeholder="marzban">
          </div>
        </div>

        <div class="form__group">
          <label for="f-desc">Описание (опционально)</label>
          <textarea id="f-desc" name="description" class="form__control" rows="2"
                    placeholder="Что делает скрипт"><?= h((string)($form_data['description'] ?? '')) ?></textarea>
        </div>

        <div class="form__group">
          <label for="f-content">Bash-скрипт</label>
          <textarea id="f-content" name="content" class="form__control script-content"
                    rows="20" required spellcheck="false"
                    placeholder="#!/usr/bin/env bash&#10;set -euo pipefail&#10;…"><?= h((string)($form_data['content'] ?? $default_content)) ?></textarea>
        </div>

        <div class="editor__foot">
          <label class="checkbox">
            <input type="checkbox" name="is_public" id="f-public" value="1"
                   <?= !isset($form_data) || !empty($form_data['is_public']) ? 'checked' : '' ?>>
            публичный (доступен через /s.php)
          </label>
          <div class="editor__buttons">
            <button type="button" class="btn-cancel js-cancel">Отмена</button>
            <button type="submit" name="save" class="btn-primary">Сохранить</button>
          </div>
        </div>
      </form>
    </dialog>

    <!-- ===================== JS поведение ===================== -->
    <script type="application/json" id="scripts-data"><?= $scripts_json ?></script>
    <script>
    (function () {
      const dialog = document.getElementById('editor');
      const form   = document.getElementById('editor-form');
      const data   = JSON.parse(document.getElementById('scripts-data').textContent || '{}');
      const defaultContent = <?= json_encode($default_content, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT) ?>;

      function open(id) {
        if (id && data[id]) {
          const s = data[id];
          form.querySelector('#editor-title').textContent = 'Редактирование: ' + s.title;
          form.querySelector('#f-id').value          = s.id;
          form.querySelector('#f-title').value       = s.title;
          form.querySelector('#f-slug').value        = s.slug;
          form.querySelector('#f-desc').value        = s.description || '';
          form.querySelector('#f-content').value     = s.content;
          form.querySelector('#f-public').checked    = !!Number(s.is_public);
        } else {
          form.querySelector('#editor-title').textContent = 'Новый скрипт';
          form.querySelector('#f-id').value       = 0;
          form.querySelector('#f-title').value    = '';
          form.querySelector('#f-slug').value     = '';
          form.querySelector('#f-desc').value     = '';
          form.querySelector('#f-content').value  = defaultContent;
          form.querySelector('#f-public').checked = true;
        }
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        setTimeout(() => form.querySelector('#f-title').focus(), 50);
      }

      function close() {
        if (typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
      }

      document.querySelectorAll('.js-new-script')
        .forEach(b => b.addEventListener('click', () => open(null)));
      document.querySelectorAll('.js-edit-script')
        .forEach(b => b.addEventListener('click', () => open(parseInt(b.dataset.id, 10))));
      document.querySelectorAll('.js-cancel')
        .forEach(b => b.addEventListener('click', close));

      // Tab внутри textarea с кодом — вставлять отступ, не уходить с поля
      const content = form.querySelector('#f-content');
      content.addEventListener('keydown', e => {
        if (e.key === 'Tab') {
          e.preventDefault();
          const s = content.selectionStart, e2 = content.selectionEnd;
          content.value = content.value.slice(0, s) + '  ' + content.value.slice(e2);
          content.selectionStart = content.selectionEnd = s + 2;
        }
      });

      // Кнопки "копировать"
      document.querySelectorAll('.js-copy').forEach(btn => {
        btn.addEventListener('click', async () => {
          try {
            await navigator.clipboard.writeText(btn.dataset.copy);
            const t = btn.textContent;
            btn.textContent = 'скопировано';
            setTimeout(() => btn.textContent = t, 1500);
          } catch {
            // fallback для http-локалки без clipboard API
            const ta = document.createElement('textarea');
            ta.value = btn.dataset.copy;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
            btn.textContent = 'скопировано';
          }
        });
      });

      // Если модалка открыта серверно (?action=add/edit или ошибка) — поднять её как modal
      if (dialog.hasAttribute('open') && typeof dialog.showModal === 'function') {
        dialog.removeAttribute('open');
        dialog.showModal();
      }
    })();
    </script>
<?php });


// ---------------------------------------------------------------------------
// Общий layout.
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
    .block        { padding: 18px; }
    .meta         { color:#888; font-size:13px; }
    .meta-count   { color:#888; font-size:13px; font-weight:normal; margin-left:6px; }
    .ok           { background:#e6f7e6; color:#226622; padding:10px 14px; border-radius:4px; margin-bottom:14px; }
    .err          { background:#fdecec; color:#992222; padding:10px 14px; border-radius:4px; margin-bottom:14px; }
    .empty        { color:#888; padding:20px 0; }

    .admin-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }
    .admin-header h3 { margin:0; }
    .admin-actions   { display:flex; align-items:center; gap:10px; }

    .btn-add      { background:#226622; color:#fff; border:0; border-radius:4px;
                    padding:8px 16px; cursor:pointer; font-size:14px; font-weight:500; }
    .btn-add:hover { background:#2a7a2a; }
    .btn-logout   { color:#888; text-decoration:none; font-size:13px; }
    .btn-logout:hover { color:#333; }

    /* карточки */
    .cards        { display:flex; flex-direction:column; gap:12px; }
    .card         { border:1px solid #e3e3e3; border-radius:6px; padding:14px 16px; background:#fff;
                    transition: box-shadow .15s, border-color .15s; }
    .card:hover   { border-color:#bbb; }
    .card.highlight { border-color:#226622; box-shadow:0 0 0 2px rgba(34,102,34,.15); }
    .card__head   { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }
    .card__title h4 { margin:0 0 4px 0; font-size:16px; }
    .card__desc   { margin:8px 0; color:#444; font-size:14px; }
    .card__actions { display:flex; gap:6px; flex-shrink:0; }
    .badge-private { display:inline-block; font-size:11px; background:#999; color:#fff;
                     padding:2px 6px; border-radius:3px; vertical-align:middle; margin-left:4px; font-weight:normal; }

    .btn-edit     { background:#f3f3f3; border:1px solid #ddd; border-radius:3px;
                    padding:4px 10px; cursor:pointer; font-size:13px; color:#444; }
    .btn-edit:hover { background:#e8e8e8; }
    .btn-del      { background:#cc3333; color:#fff; border:0; border-radius:3px;
                    padding:4px 10px; cursor:pointer; font-size:14px; line-height:1; }
    .btn-del:hover { background:#dd4444; }

    /* команда для копирования */
    .install-cmd  { background:#1e1e1e; color:#e0e0e0; padding:10px 12px; border-radius:4px;
                    font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
                    font-size:13px; overflow-x:auto; position:relative; white-space:pre;
                    margin-top:10px; }
    .install-cmd .copy-btn { position:absolute; right:6px; top:6px; padding:3px 10px;
                             background:#444; color:#fff; border:0; border-radius:3px;
                             font-size:12px; cursor:pointer; font-family:inherit; }
    .install-cmd .copy-btn:hover { background:#666; }
    .saved-banner .install-cmd { margin-top:8px; }

    /* модалка-редактор */
    .editor       { border:0; border-radius:8px; padding:0; width:min(900px, 95vw);
                    max-height: 90vh; box-shadow: 0 10px 40px rgba(0,0,0,.2); }
    .editor::backdrop { background: rgba(0,0,0,.4); }
    .editor .form { padding: 20px 24px; display:flex; flex-direction:column; gap:12px;
                    max-height: 90vh; overflow:auto; }
    .editor__head { display:flex; justify-content:space-between; align-items:center; }
    .editor__head h3 { margin:0; font-size:18px; }
    .btn-close    { background:transparent; border:0; font-size:24px; cursor:pointer;
                    color:#888; line-height:1; padding:0 6px; }
    .btn-close:hover { color:#000; }

    .form__row    { display:flex; gap:12px; }
    .form__row .form__group { flex:1; }
    .form__row .form__group--grow { flex:2; }
    .form        label { display:block; font-size:13px; color:#555; margin-bottom:4px; }
    .form__group { margin:0; }
    .form__control { width:100%; box-sizing:border-box; padding:8px 10px;
                     border:1px solid #ccc; border-radius:4px; font-size:14px; }
    .form__control:focus { outline:0; border-color:#226622; }
    .script-content { font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
                      font-size:13px; background:#1e1e1e; color:#e0e0e0; border-color:#333;
                      tab-size:2; }
    .checkbox     { display:flex; align-items:center; gap:6px; font-size:13px; color:#555; }

    .editor__foot { display:flex; justify-content:space-between; align-items:center; gap:12px;
                    padding-top:6px; border-top:1px solid #eee; margin-top:4px; padding-top:14px; }
    .editor__buttons { display:flex; gap:8px; }
    .btn-primary  { background:#226622; color:#fff; border:0; border-radius:4px;
                    padding:8px 18px; cursor:pointer; font-size:14px; font-weight:500; }
    .btn-primary:hover { background:#2a7a2a; }
    .btn-cancel   { background:#f3f3f3; border:1px solid #ddd; border-radius:4px;
                    padding:8px 14px; cursor:pointer; font-size:14px; color:#444; }
    .btn-cancel:hover { background:#e8e8e8; }
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
