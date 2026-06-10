<?php
// Общая инициализация для админки скриптов:
// - SQLite-БД (создаётся автоматически)
// - сессионная авторизация + CSRF
// - хелперы

declare(strict_types=1);

const SCRIPTS_DB_PATH     = __DIR__ . '/../data/scripts.db';
const SCRIPTS_CONFIG_PATH = __DIR__ . '/scripts_config.php';
const SCRIPTS_SLUG_RE     = '/^[a-z0-9][a-z0-9-]{0,63}$/';

function scripts_db(): PDO {
    static $pdo = null;
    if ($pdo !== null) return $pdo;
    $dir = dirname(SCRIPTS_DB_PATH);
    if (!is_dir($dir)) {
        mkdir($dir, 0700, true);
    }
    $pdo = new PDO('sqlite:' . SCRIPTS_DB_PATH);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    $pdo->exec("CREATE TABLE IF NOT EXISTS scripts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        slug        TEXT    NOT NULL UNIQUE,
        title       TEXT    NOT NULL,
        description TEXT    NOT NULL DEFAULT '',
        content     TEXT    NOT NULL,
        is_public   INTEGER NOT NULL DEFAULT 1,
        runs_count  INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )");
    return $pdo;
}

function scripts_admin_pass_hash(): ?string {
    if (!file_exists(SCRIPTS_CONFIG_PATH)) return null;
    require SCRIPTS_CONFIG_PATH;
    return defined('SCRIPTS_ADMIN_PASS_HASH') ? SCRIPTS_ADMIN_PASS_HASH : null;
}

function scripts_set_admin_password(string $plain): void {
    $hash = password_hash($plain, PASSWORD_DEFAULT);
    $code = "<?php\n// автогенерирован script_admin.php — не правь руками\n"
          . "define('SCRIPTS_ADMIN_PASS_HASH', " . var_export($hash, true) . ");\n";
    $dir = dirname(SCRIPTS_CONFIG_PATH);
    if (!is_dir($dir)) mkdir($dir, 0700, true);
    file_put_contents(SCRIPTS_CONFIG_PATH, $code, LOCK_EX);
    @chmod(SCRIPTS_CONFIG_PATH, 0600);
}

function scripts_session_start(): void {
    if (session_status() === PHP_SESSION_NONE) {
        session_name('SCRIPTS_ADMIN');
        session_set_cookie_params([
            'httponly' => true,
            'samesite' => 'Strict',
            'secure'   => (($_SERVER['HTTPS'] ?? '') === 'on'),
        ]);
        session_start();
    }
}

function scripts_is_logged_in(): bool {
    scripts_session_start();
    return !empty($_SESSION['scripts_admin']);
}

function scripts_require_login(): void {
    if (!scripts_is_logged_in()) {
        header('Location: /script_admin.php?login=1');
        exit;
    }
}

function scripts_csrf_token(): string {
    scripts_session_start();
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['csrf'];
}

function scripts_csrf_check(): void {
    scripts_session_start();
    if (empty($_POST['csrf']) || !hash_equals($_SESSION['csrf'] ?? '', (string)$_POST['csrf'])) {
        http_response_code(400);
        exit('CSRF token mismatch');
    }
}

function scripts_slugify(string $s): string {
    $s = strtolower(trim($s));
    $s = preg_replace('/[^a-z0-9]+/', '-', $s) ?? '';
    $s = trim($s, '-');
    return substr($s !== '' ? $s : 'script', 0, 64);
}

function scripts_base_url(): string {
    $scheme = (($_SERVER['HTTPS'] ?? '') === 'on') ? 'https' : 'http';
    $host   = $_SERVER['HTTP_HOST'] ?? 'localhost';
    return $scheme . '://' . $host;
}

function h(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
