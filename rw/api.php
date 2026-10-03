<?php
// Прокси от веб-интерфейса работника (/rw/) к API панели Remnawave.
//
// Зачем: бэкенд Remnawave разрешает CORS только со своего домена, поэтому браузер
// не может ходить в API напрямую с nodewiki.info. Прокси:
//   - пропускает только разрешённые разделы API (ноды, профили, сквады, хосты, keygen, сниппеты);
//   - пробрасывает токен работника как есть (сам ничего не хранит);
//   - скрывает от работника реальный адрес панели.
// Права всё равно проверяет сама панель — по скоупам API-токена.
//
// Настройка: скопируй includes/rw_config.example.php -> includes/rw_config.php и впиши адрес панели.

declare(strict_types=1);

const RW_CONFIG_PATH = __DIR__ . '/../includes/rw_config.php';

// Разрешённые разделы API. Всё остальное (users, tokens, settings, ...) отклоняется ещё до панели.
const RW_ALLOWED_PATH_RE = '#^/api/(nodes|config-profiles|internal-squads|hosts|keygen|snippets)(/[A-Za-z0-9._-]+)*$#';
const RW_ALLOWED_METHODS = ['GET', 'POST', 'PATCH', 'DELETE'];
const RW_MAX_BODY_BYTES  = 2 * 1024 * 1024;

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function rw_fail(int $status, string $message): void {
    http_response_code($status);
    echo json_encode(['message' => $message, 'statusCode' => $status], JSON_UNESCAPED_UNICODE);
    exit;
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
if (!preg_match(RW_ALLOWED_PATH_RE, $path) || strpos($path, '..') !== false) {
    rw_fail(403, 'Этот раздел API недоступен через интерфейс работника');
}

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

// Query-параметры (кроме p) пробрасываем как есть
$query = $_GET;
unset($query['p']);
$url = rtrim(RW_PANEL_URL, '/') . $path . ($query ? '?' . http_build_query($query) : '');

$headers = [
    'Authorization: ' . $auth,
    'Accept: application/json',
    // Бэкенд Remnawave требует, чтобы запрос пришёл через reverse proxy по HTTPS.
    // Если RW_PANEL_URL — публичный https-адрес панели, эти заголовки выставит её nginx/caddy.
    'X-Forwarded-Proto: https',
    'X-Forwarded-For: ' . ($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1'),
];
if ($body !== null && $body !== '') {
    $headers[] = 'Content-Type: application/json';
}
if (defined('RW_EXTRA_HEADERS') && is_array(RW_EXTRA_HEADERS)) {
    foreach (RW_EXTRA_HEADERS as $name => $value) {
        $headers[] = $name . ': ' . $value;
    }
}

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
    $err = error_get_last()['message'] ?? 'неизвестная ошибка';
    rw_fail(502, 'Панель недоступна: ' . $err);
}

$status = 502;
foreach ($http_response_header ?? [] as $line) {
    if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
        $status = (int)$m[1]; // последний статус (после возможных 1xx)
    }
}

http_response_code($status);
echo $response;
