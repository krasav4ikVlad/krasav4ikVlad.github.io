"""Шифрованные ссылки подключения: Happ и INCY.

Перенесено из utils.py (`encrypt_url`, `encrypt_url_with_rsa`, `encrypt_url_incy`).
Три способа собраны в один класс, потому что вызывающему коду важен ответ на
один вопрос — «дай ссылку для этого приложения», а не то, каким путём она
получена.

Отличия от оригинала:

* общий httpx-клиент вместо `aiohttp.ClientSession()` на каждый вызов;
* RSA-вариант используется как запасной для Happ: если внешний сервис
  crypto.happ.su не ответил, а ключ настроен, ссылка шифруется локально
  (раньше эти две функции жили отдельно и вторая не вызывалась вообще);
* путь к node-энкодеру INCY — из конфига, а не константой в коде: на Windows
  и в тестовом контуре его просто нет, и это не должно ронять бота;
* про ошибку узнаёт вызывающий: INCY бросает LinkEncryptionError, потому что
  выдать вместо incy://-ссылки обычный URL — значит показать пользователю
  ссылку, которую его приложение не примет.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from app.core.errors import AppError

log = logging.getLogger(__name__)

HAPP_API = 'https://crypto.happ.su/api-v2.php'
# ограничение RSA-1024 с PKCS1v15: длиннее просто не поместится в блок
RSA_MAX_URL = 450


class LinkEncryptionError(AppError):
    user_message = ('Не удалось собрать ссылку для этого приложения. '
                    'Попробуйте другое или напишите в поддержку.')


class LinkEncryptor:
    """Шифрование ссылок подключения под конкретное приложение."""

    def __init__(self, http, *, rsa_public_key: str = '', incy_script: str = '',
                 incy_cwd: str = '', provider: str = 'RS VPN', api_url: str = HAPP_API):
        self._http = http
        self._rsa_key = rsa_public_key
        self._incy_script = incy_script
        self._incy_cwd = incy_cwd or None
        self._provider = provider
        self._api_url = api_url

    # ── Happ ────────────────────────────────────────────────────────────────
    async def happ(self, url: str) -> str:
        """Ссылка для Happ. Не получилось зашифровать — отдаём обычную.

        Happ понимает и открытый URL, поэтому здесь запасной вариант безопасен:
        пользователь в любом случае получит рабочую ссылку.
        """
        try:
            response = await self._http.post(
                self._api_url, json={'url': url},
                headers={'Content-Type': 'application/json'}, timeout=10)
            if response.status_code == 200:
                encrypted = (response.json() or {}).get('encrypted_link')
                if encrypted:
                    return encrypted
                log.warning('happ API не вернул encrypted_link')
            else:
                log.warning('happ API вернул статус %s', response.status_code)
        except Exception as exc:  # сеть, таймаут, битый JSON — всё равно нужна ссылка
            log.warning('шифрование ссылки Happ не удалось: %s', exc)

        return self.happ_rsa(url) or url

    def happ_rsa(self, url: str) -> str:
        """Локальное шифрование ключом панели: happ://crypt3/<base64>.

        Пустая строка вместо исключения: это запасной путь внутри happ(),
        и его недоступность не повод показывать пользователю ошибку.
        """
        if not self._rsa_key or len(url) > RSA_MAX_URL:
            return ''
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            key = serialization.load_pem_public_key(self._rsa_key.encode('utf-8'))
            ciphertext = key.encrypt(url.encode('utf-8'), padding.PKCS1v15())
            return f'happ://crypt3/{base64.b64encode(ciphertext).decode("ascii")}'
        except BaseException as exc:  # noqa: BLE001 - см. ниже
            # именно BaseException: при битой сборке cryptography pyo3 бросает
            # PanicException, которая наследуется не от Exception. Экран
            # подписки не должен падать из-за запасного пути шифрования.
            log.warning('локальное шифрование ссылки не удалось: %s', exc)
            return ''

    # ── INCY ────────────────────────────────────────────────────────────────
    async def incy(self, url: str) -> str:
        """Ссылка incy:// от node-энкодера."""
        if not self._incy_script:
            raise LinkEncryptionError(
                'INCY_ENCODER не задан',
                user_message='Подключение через INCY сейчас недоступно, '
                             'воспользуйтесь Happ.')

        try:
            proc = await asyncio.create_subprocess_exec(
                'node', self._incy_script, url, self._provider,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=self._incy_cwd)
            out, err = await proc.communicate()
        except (OSError, ValueError) as exc:      # нет node или нет файла скрипта
            raise LinkEncryptionError(
                f'не удалось запустить энкодер INCY: {exc}',
                user_message='Подключение через INCY сейчас недоступно, '
                             'воспользуйтесь Happ.') from exc

        result = out.decode(errors='replace').strip()
        # энкодер отдал валидную ссылку — принимаем её, даже если node потом
        # упал на teardown (returncode -6 при корректном выводе)
        if result.startswith('incy://'):
            return result

        raise LinkEncryptionError(
            f'incy encode failed (code={proc.returncode}): '
            f'err={err.decode(errors="replace")!r} out={result!r}')

    # ── выбор приложения ────────────────────────────────────────────────────
    APPS = {'happ': 'Happ', 'incy': 'INCY'}

    async def for_app(self, app: str, url: str) -> str:
        """Единственная точка входа для хендлеров: ссылка под выбранное приложение."""
        if app == 'incy':
            return await self.incy(url)
        return await self.happ(url)
