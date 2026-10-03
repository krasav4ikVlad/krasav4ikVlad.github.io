<?php
// Настройка прокси для интерфейса работника (/rw/).
// Скопируй в includes/rw_config.php (он в .gitignore) и впиши адрес своей панели:
//   cp includes/rw_config.example.php includes/rw_config.php

// Публичный https-адрес панели Remnawave, без /api и без слэша в конце.
define('RW_PANEL_URL', 'https://panel.example.com');

// Необязательно: доп. заголовки к каждому запросу в панель.
// Например, если панель закрыта nginx-ом по секретной cookie:
//   define('RW_EXTRA_HEADERS', ['Cookie' => 'имя_куки=значение']);
define('RW_EXTRA_HEADERS', []);
