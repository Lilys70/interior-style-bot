"""Тесты прокси-настроек: PROXY_URL из .env и сессия aiogram.

Внешняя сеть не нужна: проверяются чтение переменной окружения,
создание сессии и текст подсказки при сетевой ошибке.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import bot as bot_module
from aiohttp import ClientConnectorError


class TestReadProxy(unittest.TestCase):
    """PROXY_URL не задан, пустой или с пробелами — прокси не нужен."""

    def test_no_variable_means_no_proxy(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(bot_module.read_proxy())

    def test_empty_value_means_no_proxy(self):
        with mock.patch.dict(os.environ, {"PROXY_URL": ""}, clear=True):
            self.assertIsNone(bot_module.read_proxy())

    def test_spaces_mean_no_proxy(self):
        with mock.patch.dict(os.environ, {"PROXY_URL": "   "}, clear=True):
            self.assertIsNone(bot_module.read_proxy())

    def test_value_is_stripped_and_returned(self):
        with mock.patch.dict(
            os.environ,
            {"PROXY_URL": " socks5://127.0.0.1:1080 "},
            clear=True,
        ):
            self.assertEqual(
                bot_module.read_proxy(), "socks5://127.0.0.1:1080"
            )


class TestCreateSession(unittest.IsolatedAsyncioTestCase):
    """Сессия создаётся с прокси и без, секреты не попадают в лог."""

    async def close(self, session):
        await session.close()

    async def test_session_without_proxy(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertLogs(level="INFO") as logs:
                session = bot_module.create_session()

        self.addAsyncCleanup(self.close, session)
        self.assertIsNone(session.proxy)
        output = "\n".join(logs.output)
        self.assertIn("Прокси не задан", output)
        self.assertNotIn("socks5", output)

    async def test_session_with_proxy(self):
        proxy = "socks5://user:secret@127.0.0.1:1080"
        with mock.patch.dict(os.environ, {"PROXY_URL": proxy}, clear=True):
            with self.assertLogs(level="INFO") as logs:
                session = bot_module.create_session()

        self.addAsyncCleanup(self.close, session)
        self.assertEqual(session.proxy, proxy)
        output = "\n".join(logs.output)
        self.assertIn("Прокси включён", output)
        # Ни URL, ни логин с паролем в лог не пишутся.
        self.assertNotIn("secret", output)
        self.assertNotIn("127.0.0.1", output)

    async def test_missing_aiohttp_socks_gives_hint(self):
        """Без пакета aiohttp-socks aiogram падает — подсказываем команду."""
        with mock.patch.dict(
            os.environ, {"PROXY_URL": "socks5://127.0.0.1:1080"}, clear=True
        ):
            with mock.patch.object(
                bot_module,
                "AiohttpSession",
                side_effect=RuntimeError("install aiohttp-socks"),
            ):
                with self.assertRaises(RuntimeError) as context:
                    bot_module.create_session()

        message = str(context.exception)
        self.assertIn("aiohttp-socks", message)
        self.assertIn("pip install", message)


class TestNetworkErrorMessage(unittest.TestCase):
    """Подсказка при сетевой ошибке называет причину и выход."""

    def test_message_explains_proxy(self):
        error = ClientConnectorError(
            SimpleNamespace(
                host="api.telegram.org",
                port=443,
                is_ssl=True,
                ssl=True,
            ),
            OSError("нет маршрута"),
        )

        message = bot_module.network_error_message(error)

        self.assertIn("api.telegram.org", message)
        self.assertIn("ClientConnectorError", message)
        self.assertIn("PROXY_URL", message)
        self.assertIn("socks5://host:port", message)

    def test_message_explains_broken_proxy(self):
        class FakeProxyError(Exception):
            """Заглушка вместо python_socks.ProxyError."""

        with mock.patch.object(bot_module, "ProxyError", FakeProxyError):
            message = bot_module.network_error_message(
                FakeProxyError("прокси не отвечает")
            )

        self.assertIn("через прокси", message)
        self.assertIn("FakeProxyError", message)
        self.assertIn("PROXY_URL", message)


class TestNetworkErrors(unittest.TestCase):
    """Список перехватываемых сетевых ошибок пополняется прокси-ошибкой."""

    def test_proxy_error_in_network_errors(self):
        self.assertIn(ClientConnectorError, bot_module.NETWORK_ERRORS)
        self.assertIn(
            bot_module.TelegramNetworkError, bot_module.NETWORK_ERRORS
        )
        if bot_module.ProxyError is not None:
            self.assertIn(bot_module.ProxyError, bot_module.NETWORK_ERRORS)
