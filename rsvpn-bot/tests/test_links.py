"""Шифрование ссылок подключения (Happ / INCY)."""

import pytest

from app.integrations.vpn.links import LinkEncryptionError, LinkEncryptor

URL = 'https://connect.rsvps.tech/abc123'


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response or FakeResponse(payload={'encrypted_link': 'happ://enc'})
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


async def test_happ_returns_encrypted_link():
    links = LinkEncryptor(FakeHttp())
    assert await links.happ(URL) == 'happ://enc'


async def test_happ_falls_back_to_plain_url_when_service_is_down():
    """Happ понимает и открытый URL — молчаливая ошибка хуже рабочей ссылки."""
    links = LinkEncryptor(FakeHttp(error=TimeoutError('нет сети')))
    assert await links.happ(URL) == URL


async def test_happ_falls_back_to_local_rsa_when_key_is_configured():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except BaseException as exc:      # pyo3 роняет PanicException мимо Exception
        pytest.skip(f'cryptography недоступна: {exc}')

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    links = LinkEncryptor(FakeHttp(response=FakeResponse(status_code=500)),
                          rsa_public_key=pem)
    assert (await links.happ(URL)).startswith('happ://crypt3/')


async def test_rsa_is_skipped_for_too_long_urls():
    links = LinkEncryptor(FakeHttp(), rsa_public_key='не важно')
    assert links.happ_rsa('x' * 500) == ''


async def test_incy_without_encoder_says_so_instead_of_giving_a_wrong_link():
    """Подсунуть вместо incy:// обычный URL — значит выдать ссылку, которую
    приложение не примет. Лучше честная ошибка."""
    links = LinkEncryptor(FakeHttp(), incy_script='')

    with pytest.raises(LinkEncryptionError) as exc:
        await links.incy(URL)
    assert 'INCY' in exc.value.user_message


async def test_incy_missing_node_binary_is_reported():
    links = LinkEncryptor(FakeHttp(), incy_script='/nonexistent/incy_encode.mjs',
                          incy_cwd='/nonexistent')

    with pytest.raises(LinkEncryptionError):
        await links.incy(URL)


async def test_for_app_picks_the_right_encoder():
    links = LinkEncryptor(FakeHttp())
    assert await links.for_app('happ', URL) == 'happ://enc'
    assert await links.for_app('что-то ещё', URL) == 'happ://enc'
