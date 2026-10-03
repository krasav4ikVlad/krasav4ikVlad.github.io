<?php
// Прокси от веб-интерфейса работника (/rw/) к API панели Remnawave и к DNS Cloudflare.
//
// Зачем: бэкенд Remnawave разрешает CORS только со своего домена, поэтому браузер
// не может ходить в API напрямую. Прокси:
//   - пропускает только разрешённые разделы API (ноды, профили, сквады, хосты, keygen, сниппеты);
//   - пробрасывает токен работника как есть (сам ничего не хранит);
//   - скрывает от работника реальный адрес панели.
// Права всё равно проверяет сама панель — по скоупам API-токена.
//
// Cloudflare (?p=/cf/...): ключ Cloudflare лежит только на сервере (CF_API_TOKEN в конфиге),
// работник его не видит. Доступ — только к DNS-записям и только с действующим токеном панели.
//
// Настройка: скопируй includes/rw_config.example.php -> includes/rw_config.php и впиши адрес панели.

declare(strict_types=1);

const RW_CONFIG_PATH = __DIR__ . '/../includes/rw_config.php';

// Разрешённые разделы API. Всё остальное (users, tokens, settings, ...) отклоняется ещё до панели.
const RW_ALLOWED_PATH_RE = '#^/api/(nodes|config-profiles|internal-squads|hosts|keygen|snippets)(/[A-Za-z0-9._-]+)*$#';
const RW_ALLOWED_METHODS = ['GET', 'POST', 'PATCH', 'DELETE'];
const RW_MAX_BODY_BYTES  = 2 * 1024 * 1024;

const CF_API_BASE = 'https://api.cloudflare.com/client/v4';
// Разрешённые запросы к Cloudflare: только зоны (чтение) и DNS-записи
const CF_ROUTES = [
    '#^/zones$#'                                           => ['GET'],
    '#^/zones/[a-f0-9]{32}$#'                              => ['GET'],
    '#^/zones/[a-f0-9]{32}/dns_records$#'                  => ['GET', 'POST'],
    '#^/zones/[a-f0-9]{32}/dns_records/[a-f0-9]{32}$#'     => ['GET', 'PATCH', 'DELETE'],
];
const CF_ALLOWED_QUERY = ['page', 'per_page', 'type', 'name', 'order', 'direction'];
const RW_AUTH_CACHE_TTL = 300; // сколько секунд помнить, что токен работника действителен

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function rw_fail(int $status, string $message): void {
    http_response_code($status);
    echo json_encode(['message' => $message, 'statusCode' => $status], JSON_UNESCAPED_UNICODE);
    exit;
}

// HTTP-запрос без зависимостей (php-curl не нужен). Возвращает [статус, тело] или [0, текст ошибки].
function rw_http(string $method, string $url, array $headers, ?string $body): array {
    $ctx = stream_context_create([
        'http' => [
            'method'          => $method,
            'header'          => implode("\r\n", $headers),
            'content'         => ($body !== null && $body !== '') ? $body : '',
            'ignore_errors'   => true, // отдавать тело и при 4xx/5xx
            'follow_location' => 0,
            'timeout'         => 60,
        ],
    ]);
    $response = @file_get_contents($url, false, $ctx);
    if ($response === false) {
        return [0, error_get_last()['message'] ?? 'неизвестная ошибка'];
    }
    $status = 502;
    foreach ($http_response_header ?? [] as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
            $status = (int)$m[1]; // последний статус (после возможных 1xx)
        }
    }
    return [$status, $response];
}

function rw_panel_headers(string $auth): array {
    $headers = [
        'Authorization: ' . $auth,
        'Accept: application/json',
        // Бэкенд Remnawave требует, чтобы запрос пришёл через reverse proxy по HTTPS.
        // Если RW_PANEL_URL — публичный https-адрес панели, эти заголовки выставит её nginx/caddy.
        'X-Forwarded-Proto: https',
        'X-Forwarded-For: ' . ($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1'),
    ];
    if (defined('RW_EXTRA_HEADERS') && is_array(RW_EXTRA_HEADERS)) {
        foreach (RW_EXTRA_HEADERS as $name => $value) {
            $headers[] = $name . ': ' . $value;
        }
    }
    return $headers;
}

// Токен работника действителен, если панель его узнаёт (401 — нет; 2xx/403 — да, просто нет скоупа).
function rw_require_valid_token(string $auth): void {
    $mark = sys_get_temp_dir() . '/rw_auth_' . hash('sha256', RW_PANEL_URL . '|' . $auth);
    if (is_file($mark) && filemtime($mark) > time() - RW_AUTH_CACHE_TTL) {
        return;
    }
    [$status, $resp] = rw_http('GET', rtrim(RW_PANEL_URL, '/') . '/api/nodes/tags', rw_panel_headers($auth), null);
    if ($status === 0 || $status >= 500) {
        rw_fail(502, 'Панель недоступна, не удалось проверить токен');
    }
    if ($status === 401) {
        @unlink($mark);
        rw_fail(401, 'Токен недействителен или истёк');
    }
    @touch($mark);
}

if (!file_exists(RW_CONFIG_PATH)) {
    rw_fail(500, 'Прокси не настроен: нет includes/rw_config.php (см. includes/rw_config.example.php)');
}
require RW_CONFIG_PATH;
if (!defined('RW_PANEL_URL') || !preg_match('#^https?://#', RW_PANEL_URL)) {
    rw_fail(500, 'Прокси не настроен: RW_PANEL_URL пуст или некорректен');
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if (!in_array($method, RW_ALLOWED_METHODS, true)) {
    rw_fail(405, 'Метод не разрешён');
}

$path = (string)($_GET['p'] ?? '');

$auth = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
if (!preg_match('#^Bearer [A-Za-z0-9._~+/=-]{10,4096}$#', $auth)) {
    rw_fail(401, 'Нет токена');
}

$body = null;
if ($method !== 'GET') {
    $body = file_get_contents('php://input', false, null, 0, RW_MAX_BODY_BYTES + 1);
    if ($body !== false && strlen($body) > RW_MAX_BODY_BYTES) {
        rw_fail(413, 'Слишком большой запрос');
    }
}

/* ------------------------------------------------------------------------
 * Cloudflare DNS
 * ------------------------------------------------------------------------ */
if (strpos($path, '/cf/') === 0) {
    if (!defined('CF_API_TOKEN') || CF_API_TOKEN === '') {
        rw_fail(501, 'Cloudflare не подключён: администратор не указал CF_API_TOKEN');
    }
    $cfPath = substr($path, 3);
    $allowed = false;
    foreach (CF_ROUTES as $re => $methods) {
        if (preg_match($re, $cfPath) && in_array($method, $methods, true)) {
            $allowed = true;
            break;
        }
    }
    if (!$allowed) {
        rw_fail(403, 'Это действие с Cloudflare недоступно');
    }
    if ($body !== null && $body !== '' && !is_array(json_decode($body, true))) {
        rw_fail(400, 'Некорректный JSON');
    }

    rw_require_valid_token($auth);

    $query = array_intersect_key($_GET, array_flip(CF_ALLOWED_QUERY));
    $cfBase = defined('CF_API_BASE_URL') ? CF_API_BASE_URL : CF_API_BASE; // переопределение — только для тестов
    $url = $cfBase . $cfPath . ($query ? '?' . http_build_query($query) : '');
    $headers = ['Authorization: Bearer ' . CF_API_TOKEN, 'Accept: application/json'];
    if ($body !== null && $body !== '') {
        $headers[] = 'Content-Type: application/json';
    }
    [$status, $resp] = rw_http($method, $url, $headers, $body);
    if ($status === 0) {
        rw_fail(502, 'Cloudflare недоступен: ' . $resp);
    }
    http_response_code($status);
    echo $resp;
    exit;
}

/* ------------------------------------------------------------------------
 * Панель Remnawave
 * ------------------------------------------------------------------------ */

// Список плагинов нод — только чтение (для выбора плагина у ноды)
$isPluginList = $path === '/api/node-plugins' && $method === 'GET';
if (!$isPluginList && (!preg_match(RW_ALLOWED_PATH_RE, $path) || strpos($path, '..') !== false)) {
    rw_fail(403, 'Этот раздел API недоступен через интерфейс работника');
}

// Query-параметры (кроме p) пробрасываем как есть
$query = $_GET;
unset($query['p']);
$url = rtrim(RW_PANEL_URL, '/') . $path . ($query ? '?' . http_build_query($query) : '');

$headers = rw_panel_headers($auth);
if ($body !== null && $body !== '') {
    $headers[] = 'Content-Type: application/json';
}

[$status, $response] = rw_http($method, $url, $headers, $body);
if ($status === 0) {
    rw_fail(502, 'Панель недоступна: ' . $response);
}

http_response_code($status);
echo $response;
