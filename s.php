<?php
// Раздача сырого bash-скрипта по slug.
// Использование:
//   curl -sSL https://your-site/s.php?slug=marzban | bash
//   bash <(curl -sSL https://your-site/s.php?slug=marzban)

require __DIR__ . '/includes/scripts_init.php';

header('Content-Type: text/plain; charset=utf-8');
header('Cache-Control: no-store, must-revalidate');
header('X-Content-Type-Options: nosniff');

$slug = strtolower(trim((string)($_GET['slug'] ?? '')));
if (!preg_match(SCRIPTS_SLUG_RE, $slug)) {
    http_response_code(400);
    echo "# bad slug\n# usage: /s.php?slug=NAME\n";
    exit;
}

try {
    $pdo  = scripts_db();
    $stmt = $pdo->prepare('SELECT id, content FROM scripts WHERE slug = ? AND is_public = 1');
    $stmt->execute([$slug]);
    $row = $stmt->fetch();
} catch (Throwable $e) {
    http_response_code(500);
    echo "# database error\n";
    exit;
}

if (!$row) {
    http_response_code(404);
    echo "# script not found: $slug\n";
    exit;
}

try {
    $pdo->prepare('UPDATE scripts SET runs_count = runs_count + 1 WHERE id = ?')
        ->execute([$row['id']]);
} catch (Throwable $e) {
    // не критично — продолжаем отдавать скрипт
}

$content = (string)$row['content'];
// нормализация переводов строк (на случай если редактировали из винды)
$content = str_replace(["\r\n", "\r"], "\n", $content);
if (strncmp($content, '#!', 2) !== 0) {
    echo "#!/usr/bin/env bash\nset -euo pipefail\n";
}
echo $content;
if ($content === '' || substr($content, -1) !== "\n") echo "\n";
