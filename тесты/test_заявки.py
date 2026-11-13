"""Тесты шага 5: заявки в JSON и форма заявки в боте.

Форма состоит из трёх шагов: имя → время и формат связи → контакт.
Команда внутри формы (`/start`, `/reset`) прерывает заявку и возвращает
в начало — это проверяет отдельный класс TestExitFormOnCommand.

Внешнего Telegram здесь нет: Message и CallbackQuery заменены
заглушками, FSMContext взят настоящий на MemoryStorage. Файл заявок
подменяется на временную папку, поэтому реальные данные не трогаются.
"""

import itertools
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bot as bot_module
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from данные.questions import calculate_result
from хранилище import заявки as applications

# Один и тот же ключ хранилища на все тесты: имитируем одного пользователя.
STORAGE_KEY = StorageKey(bot_id=1, chat_id=42, user_id=42)


class SentMessage:
    """Сообщение, которое бот отправил: текст и клавиатура."""

    _ids = itertools.count(100)

    def __init__(self, text="", reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        self.message_id = next(SentMessage._ids)


class FakeMessage:
    """Заглушка aiogram.types.Message: запоминает, что бот отправил."""

    def __init__(self, text=None, user_id=42, message_id=1):
        self.text = text
        self.message_id = message_id
        self.from_user = SimpleNamespace(id=user_id)
        self.sent = []
        self.deleted = 0

    async def answer(self, text, reply_markup=None, **kwargs):
        message = SentMessage(text=text, reply_markup=reply_markup)
        self.sent.append(message)
        return message

    async def answer_photo(
        self, photo=None, caption=None, reply_markup=None, **kwargs
    ):
        message = SentMessage(text=caption or "", reply_markup=reply_markup)
        message.photo = photo
        self.sent.append(message)
        return message

    async def delete(self):
        self.deleted += 1


class FakeCallbackQuery:
    """Заглушка CallbackQuery: помнит ответы в alert."""

    def __init__(self, data, message=None):
        self.data = data
        self.message = message
        self.alerts = []

    async def answer(self, text=None, show_alert=False):
        self.alerts.append(text)


class ApplicationsFileTestCase(unittest.TestCase):
    """Общая обвязка: временная папка вместо хранилища/заявки.json."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.applications_file = Path(self.tmp_dir.name) / "заявки.json"

        patcher = mock.patch.object(
            applications, "APPLICATIONS_FILE", self.applications_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def read_raw(self) -> str:
        return self.applications_file.read_text(encoding="utf-8")

    def read_json(self) -> list:
        return json.loads(self.read_raw())

    def backups(self) -> list:
        return sorted(self.tmp_dir_path().glob("заявки.битые.*.json"))

    def tmp_dir_path(self) -> Path:
        return Path(self.tmp_dir.name)


class TestApplicationsStorage(ApplicationsFileTestCase):
    """Сохранение заявок в JSON."""

    def test_save_creates_file_with_indent_and_timestamp(self):
        applications.save_application(
            user_id=42,
            name="Анна",
            time_format="завтра после 18:00, Telegram",
            contact="+7 999 123-45-67",
            style="loft",
        )

        self.assertTrue(self.applications_file.is_file())
        raw = self.read_raw()
        # Отступ 4 пробела на уровень: объект внутри списка — 8.
        self.assertIn('\n    {\n        "timestamp"', raw)
        self.assertIn("Анна", raw)
        self.assertNotIn("\\u", raw)

        saved = self.read_json()[0]
        self.assertEqual(saved["name"], "Анна")
        self.assertEqual(
            saved["time_format"], "завтра после 18:00, Telegram"
        )
        self.assertEqual(saved["contact"], "+7 999 123-45-67")
        self.assertEqual(saved["style"], "loft")
        self.assertEqual(saved["user_id"], 42)

        # timestamp — ISO 8601, разбирается обратно в дату.
        moment = datetime.fromisoformat(saved["timestamp"])
        self.assertIsNotNone(moment.tzinfo)

    def test_saved_record_has_exactly_six_fields(self):
        """Поля записи — по ТЗ; шага «комментарий» в заявке нет."""
        applications.save_application(
            42, "Анна", "после 18:00", "@anna", "loft"
        )

        saved = self.read_json()[0]
        self.assertEqual(
            sorted(saved),
            [
                "contact",
                "name",
                "style",
                "time_format",
                "timestamp",
                "user_id",
            ],
        )
        self.assertNotIn("comment", saved)

    def test_second_application_appends(self):
        applications.save_application(
            42, "Анна", "после 18:00", "@anna", "loft"
        )
        applications.save_application(
            43, "Иван", "завтра утром", "+79990000000", "classic"
        )

        saved = self.read_json()
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[1]["name"], "Иван")
        self.assertEqual(saved[1]["time_format"], "завтра утром")

    def test_tie_style_saved_as_list(self):
        applications.save_application(
            user_id=42,
            name="Анна",
            time_format="завтра после 18:00, Telegram",
            contact="@anna",
            style=["loft", "minimalism"],
        )
        self.assertEqual(self.read_json()[0]["style"], ["loft", "minimalism"])

    def test_missing_file_gives_empty_list(self):
        self.assertEqual(applications.load_applications(), [])

    def test_broken_json_is_backed_up_and_file_rebuilt(self):
        self.applications_file.write_text(
            '[{"name": "Анна", "contact": ', encoding="utf-8"
        )

        applications.save_application(
            42, "Иван", "завтра утром", "@ivan", "loft"
        )

        saved = self.read_json()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "Иван")

        backups = self.backups()
        self.assertEqual(len(backups), 1)
        # Копия сохранила исходный битый текст.
        self.assertIn("Анна", backups[0].read_text(encoding="utf-8"))

    def test_json_not_a_list_is_backed_up(self):
        self.applications_file.write_text('{"name": "Анна"}', encoding="utf-8")

        applications.save_application(
            42, "Иван", "завтра утром", "@ivan", "loft"
        )

        self.assertEqual(len(self.read_json()), 1)
        self.assertEqual(len(self.backups()), 1)


class BotTestCase(unittest.IsolatedAsyncioTestCase):
    """Обвязка тестов бота: временный файл заявок и настоящее FSM."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.applications_file = Path(self.tmp_dir.name) / "заявки.json"

        patcher = mock.patch.object(
            applications, "APPLICATIONS_FILE", self.applications_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.state = FSMContext(key=STORAGE_KEY, storage=MemoryStorage())

    def read_json(self) -> list:
        return json.loads(self.applications_file.read_text(encoding="utf-8"))

    async def start_request(self, style="loft", title="Лофт"):
        """Готовит экран результата и нажимает «Оставить заявку»."""
        await self.state.set_data(
            {"result_style": style, "result_title": title}
        )
        callback = FakeCallbackQuery("book_consult", FakeMessage())
        await bot_module.on_book_consult(callback, self.state)
        return callback

    async def fill_request(
        self,
        name="Анна",
        time_format="завтра после 18:00, Telegram",
        contact="@anna",
    ):
        """Проходит три шага формы и возвращает последнее сообщение бота."""
        await self.start_request()
        await bot_module.on_request_name(FakeMessage(name), self.state)
        await bot_module.on_request_time_format(
            FakeMessage(time_format), self.state
        )
        message = FakeMessage(contact)
        await bot_module.on_request_contact(message, self.state)
        return message


class TestResultToRequest(BotTestCase):
    """Связка «результат теста → стиль в FSM → заявка»."""

    async def test_last_answer_keeps_style_for_request(self):
        await self.state.set_state(bot_module.TestStates.answering)
        await self.state.set_data(
            {
                "index": 6,
                "answers": [{"scores": {"scandinavian": 2}}] * 6,
                "message_id": 7,
            }
        )
        callback = FakeCallbackQuery("ans_0", FakeMessage(message_id=7))

        await bot_module.on_answer(callback, self.state)

        # Состояние прохождения снято, но стиль остался в FSM.
        self.assertIsNone(await self.state.get_state())
        data = await self.state.get_data()
        self.assertEqual(data["result_style"], "scandinavian")
        self.assertEqual(data["result_title"], "Скандинавский")

        screen = callback.message.sent[-1]
        self.assertIn("Ваш стиль — Скандинавский", screen.text)
        buttons = screen.reply_markup.inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "book_consult")
        self.assertEqual(buttons[1][0].url, bot_module.CHANNEL_URL)
        self.assertEqual(buttons[2][0].callback_data, "restart")

    async def test_tie_styles_kept_as_list(self):
        answers = (
            [{"scores": {"loft": 2}}] * 3
            + [{"scores": {"minimalism": 2}}] * 3
            + [{"scores": {"eclecticism": 2}}]
        )
        result = calculate_result(answers)

        self.assertEqual(
            bot_module.result_style_value(result), ["loft", "minimalism"]
        )
        self.assertEqual(
            bot_module.result_style_title(result), "Лофт и Минимализм"
        )


class TestRequestForm(BotTestCase):
    """Форма заявки: вопросы, валидация полей, запись в JSON."""

    async def test_book_without_result_shows_alert(self):
        callback = FakeCallbackQuery("book_consult", FakeMessage())

        await bot_module.on_book_consult(callback, self.state)

        self.assertEqual(len(callback.alerts), 1)
        self.assertIn("Сначала пройдите тест", callback.alerts[0])
        self.assertIsNone(await self.state.get_state())
        self.assertFalse(self.applications_file.exists())

    async def test_book_after_result_asks_name(self):
        callback = await self.start_request()

        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_name.state,
        )
        self.assertEqual(callback.message.deleted, 1)
        self.assertIn("Как вас зовут?", callback.message.sent[-1].text)

    async def test_name_too_short_is_rejected(self):
        await self.start_request()

        message = FakeMessage("А")
        await bot_module.on_request_name(message, self.state)

        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_name.state,
        )
        self.assertEqual(message.sent[-1].text, bot_module.NAME_ERROR_TEXT)
        data = await self.state.get_data()
        self.assertNotIn("request_name", data)

    async def test_name_too_long_is_rejected(self):
        await self.start_request()

        message = FakeMessage("А" * 65)
        await bot_module.on_request_name(message, self.state)

        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_name.state,
        )
        self.assertEqual(message.sent[-1].text, bot_module.NAME_ERROR_TEXT)

    async def test_name_moves_to_time_format(self):
        await self.start_request()

        message = FakeMessage("  Анна  ")
        await bot_module.on_request_name(message, self.state)

        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_time_format.state,
        )
        data = await self.state.get_data()
        self.assertEqual(data["request_name"], "Анна")
        self.assertEqual(
            message.sent[-1].text, bot_module.ASK_TIME_FORMAT_TEXT
        )

    async def test_name_kept_raw_and_saved_raw(self):
        """HTML в имени не экранируется в данных: экранирует экран."""
        name = "<b>Аня & Ко</b>"

        await self.fill_request(name=name)

        self.assertEqual(self.read_json()[0]["name"], name)

    async def test_time_format_validation(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)

        for time_format in ("18", "x" * 101):
            message = FakeMessage(time_format)
            await bot_module.on_request_time_format(message, self.state)
            self.assertEqual(
                await self.state.get_state(),
                bot_module.RequestStates.waiting_time_format.state,
            )
            self.assertEqual(
                message.sent[-1].text, bot_module.TIME_FORMAT_ERROR_TEXT
            )
            data = await self.state.get_data()
            self.assertNotIn("request_time_format", data)

    async def test_time_format_moves_to_contact(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)

        message = FakeMessage("  завтра после 18:00, Telegram  ")
        await bot_module.on_request_time_format(message, self.state)

        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_contact.state,
        )
        data = await self.state.get_data()
        self.assertEqual(
            data["request_time_format"], "завтра после 18:00, Telegram"
        )
        self.assertEqual(message.sent[-1].text, bot_module.ASK_CONTACT_TEXT)

    async def test_contact_validation(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)
        await bot_module.on_request_time_format(
            FakeMessage("после 18:00"), self.state
        )

        for contact in ("1a", "x" * 101):
            message = FakeMessage(contact)
            await bot_module.on_request_contact(message, self.state)
            self.assertEqual(
                await self.state.get_state(),
                bot_module.RequestStates.waiting_contact.state,
            )
            self.assertEqual(
                message.sent[-1].text, bot_module.CONTACT_ERROR_TEXT
            )
        self.assertFalse(self.applications_file.exists())

    async def test_contact_saves_application(self):
        message = await self.fill_request()

        saved = self.read_json()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "Анна")
        self.assertEqual(
            saved[0]["time_format"], "завтра после 18:00, Telegram"
        )
        self.assertEqual(saved[0]["contact"], "@anna")
        self.assertEqual(saved[0]["style"], "loft")
        self.assertEqual(saved[0]["user_id"], 42)
        self.assertNotIn("comment", saved[0])

        text = message.sent[-1].text
        self.assertIn("Спасибо! Мы свяжемся с вами", text)
        self.assertIn("Ваш стиль: Лофт", text)
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})

    async def test_contact_is_trimmed(self):
        await self.fill_request(contact="  @anna  ")

        self.assertEqual(self.read_json()[0]["contact"], "@anna")

    async def test_thanks_has_channel_and_restart(self):
        message = await self.fill_request()

        buttons = message.sent[-1].reply_markup.inline_keyboard
        self.assertEqual(buttons[0][0].url, bot_module.CHANNEL_URL)
        self.assertEqual(buttons[1][0].callback_data, "restart")

    async def test_tie_title_in_thanks(self):
        await self.state.set_data(
            {
                "result_style": ["loft", "minimalism"],
                "result_title": "Лофт и Минимализм",
            }
        )
        await self.state.set_state(bot_module.RequestStates.waiting_name)
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)
        await bot_module.on_request_time_format(
            FakeMessage("после 18:00"), self.state
        )
        message = FakeMessage("@anna")
        await bot_module.on_request_contact(message, self.state)

        self.assertIn("Ваш стиль: Лофт и Минимализм", message.sent[-1].text)
        self.assertEqual(
            self.read_json()[0]["style"], ["loft", "minimalism"]
        )

    async def test_result_title_escaped_in_thanks(self):
        """Название стиля уходит в HTML — значит, экранируется."""
        await self.state.set_data(
            {
                "result_style": "loft",
                "result_title": "<b>Лофт</b> & Ко",
            }
        )
        await self.state.set_state(bot_module.RequestStates.waiting_name)
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)
        await bot_module.on_request_time_format(
            FakeMessage("после 18:00"), self.state
        )
        message = FakeMessage("@anna")
        await bot_module.on_request_contact(message, self.state)

        text = message.sent[-1].text
        self.assertIn("&lt;b&gt;Лофт&lt;/b&gt; &amp; Ко", text)
        self.assertNotIn("<b>Лофт</b> & Ко", text)

    async def test_restart_clears_state(self):
        await self.start_request()
        callback = FakeCallbackQuery("restart", FakeMessage())

        await bot_module.on_restart(callback, self.state)

        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(
            callback.message.sent[-1].text, bot_module.WELCOME_TEXT
        )

    async def test_start_interrupts_request(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)

        await bot_module.cmd_start(FakeMessage("/start"), self.state)

        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertFalse(self.applications_file.exists())

    async def test_save_error_keeps_state_for_retry(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)
        await bot_module.on_request_time_format(
            FakeMessage("после 18:00"), self.state
        )

        message = FakeMessage("@anna")
        with mock.patch.object(
            bot_module, "save_application", side_effect=OSError("нет прав")
        ):
            await bot_module.on_request_contact(message, self.state)

        self.assertEqual(message.sent[-1].text, bot_module.SAVE_ERROR_TEXT)
        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_contact.state,
        )
        self.assertFalse(self.applications_file.exists())


class TestExitFormOnCommand(BotTestCase):
    """Команда посреди формы прерывает заявку и возвращает в начало.

    Имя, время и контакт — свободный текст, поэтому без проверки
    «/start» уходил бы в поле имени. Проверяем и сам помощник,
    и каждый из трёх обработчиков.
    """

    async def test_helper_returns_true_for_command(self):
        message = FakeMessage("/start")

        interrupted = await bot_module.exit_form_on_command(
            message, self.state
        )

        self.assertTrue(interrupted)
        self.assertEqual(message.sent[-1].text, bot_module.WELCOME_TEXT)

    async def test_helper_returns_false_for_plain_text(self):
        await self.state.set_state(bot_module.RequestStates.waiting_name)
        await self.state.set_data({"result_style": "loft"})

        interrupted = await bot_module.exit_form_on_command(
            FakeMessage("Анна"), self.state
        )

        self.assertFalse(interrupted)
        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_name.state,
        )
        data = await self.state.get_data()
        self.assertEqual(data["result_style"], "loft")

    async def test_start_on_name_step_returns_to_welcome(self):
        await self.start_request()

        message = FakeMessage("/start")
        await bot_module.on_request_name(message, self.state)

        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(message.sent[-1].text, bot_module.WELCOME_TEXT)
        self.assertFalse(self.applications_file.exists())

    async def test_reset_on_time_format_step_returns_to_welcome(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)

        message = FakeMessage("/reset")
        await bot_module.on_request_time_format(message, self.state)

        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(message.sent[-1].text, bot_module.WELCOME_TEXT)
        self.assertFalse(self.applications_file.exists())

    async def test_command_on_contact_step_saves_nothing(self):
        await self.start_request()
        await bot_module.on_request_name(FakeMessage("Анна"), self.state)
        await bot_module.on_request_time_format(
            FakeMessage("после 18:00"), self.state
        )

        message = FakeMessage("/start")
        await bot_module.on_request_contact(message, self.state)

        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(message.sent[-1].text, bot_module.WELCOME_TEXT)
        self.assertFalse(self.applications_file.exists())

    async def test_slash_inside_text_is_not_command(self):
        """Косая черта в середине — не команда: имя сохраняется."""
        await self.start_request()

        message = FakeMessage("Анна/Иван")
        await bot_module.on_request_name(message, self.state)

        self.assertEqual(
            await self.state.get_state(),
            bot_module.RequestStates.waiting_time_format.state,
        )
        data = await self.state.get_data()
        self.assertEqual(data["request_name"], "Анна/Иван")
