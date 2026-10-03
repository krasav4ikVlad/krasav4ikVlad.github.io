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

// Необязательно: управление DNS в Cloudflare (раздел «Домены»).
// Ключ хранится только здесь, работник его не видит.
// Создать: Cloudflare → My Profile → API Tokens → Create Token → шаблон «Edit zone DNS».
//   Permissions:      Zone → DNS → Edit  и  Zone → Zone → Read
//   Zone Resources:   Include → All zones (или только нужные домены)
// Оставь пустым, чтобы раздел «Домены» был выключен.
define('CF_API_TOKEN', '');
