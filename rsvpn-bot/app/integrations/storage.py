"""Копия базы — наружу, в S3-совместимое хранилище.

Копия, которая лежит на том же сервере, спасает от испорченной базы и не
спасает ровно от того, ради чего всё затевалось: аккаунт на хостинге
забанили — сервер пропал вместе со всеми снимками.

Поэтому вторая копия уезжает в объектное хранилище. Подойдёт любое
S3-совместимое: Cloudflare R2, Backblaze B2, Selectel, Timeweb, VK Cloud
— у всех одинаковый протокол, меняются только адрес и ключи. Они живут в
.env рядом с платёжными ключами, а не в настройках: настройки видны на
экране админки и лежат в той самой базе, копию которой мы и делаем.

Загрузка идёт из процесса снимка, а не из бота, и по частям (multipart
внутри boto3): четырёхсотмегабайтный файл не держится в памяти целиком.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Старые копии в хранилище убираем так же, как на диске: платить за год
# ежедневных снимков никто не подписывался.
KEEP = 14


class S3Storage:
    def __init__(self, config, client=None):
        self.config = config
        self._client = client

    @property
    def ready(self) -> bool:
        return bool(self.config and self.config.ready)

    def client(self):
        if self._client is not None:
            return self._client
        import boto3

        self._client = boto3.client(
            's3',
            endpoint_url=self.config.endpoint,
            aws_access_key_id=self.config.key,
            aws_secret_access_key=self.config.secret,
            region_name=self.config.region or 'auto')
        return self._client

    def key_for(self, name: str) -> str:
        prefix = (self.config.prefix or '').strip('/')
        return f'{prefix}/{name}' if prefix else name

    def upload(self, path) -> str:
        """Положить файл. Возвращает ключ в хранилище или '' при неудаче."""
        from pathlib import Path

        item = Path(path)
        key = self.key_for(item.name)
        try:
            self.client().upload_file(str(item), self.config.bucket, key)
        except Exception as exc:      # noqa: BLE001 — причину наверх строкой
            log.error('копия не уехала в хранилище: %s', exc)
            raise

        log.warning('копия базы в хранилище: %s/%s', self.config.bucket, key)
        return key

    def rotate(self, keep: int = KEEP) -> int:
        """Убрать старые копии. Возвращает, сколько удалили."""
        prefix = (self.config.prefix or '').strip('/')
        answer = self.client().list_objects_v2(
            Bucket=self.config.bucket, Prefix=f'{prefix}/' if prefix else '')
        items = sorted(answer.get('Contents') or [],
                       key=lambda row: row.get('LastModified') or 0,
                       reverse=True)

        removed = 0
        for row in items[keep:]:
            try:
                self.client().delete_object(Bucket=self.config.bucket,
                                            Key=row['Key'])
                removed += 1
            except Exception as exc:      # noqa: BLE001 — уборка не главное
                log.warning('старая копия %s не удалена: %s',
                            row.get('Key'), exc)
        return removed
