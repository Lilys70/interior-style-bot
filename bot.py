"""Telegram-бот «Определи свой стиль интерьера».

Учебный проект №2 Zerocoder, шаг 5: тест, результат и заявка на вводную
сессию. Вопросы и веса ответов берутся из пакета данные (questions.py,
styles.py), подсчёт и разбор ничьей — функция calculate_result(),
заявки уходят в хранилище/заявки.json, граф сценария лежит рядом
в схема_бота.json.

Запуск: python bot.py (из корня папки проекта; токен читается из .env).
Если api.telegram.org недоступен напрямую, в .env можно вписать прокси:
PROXY_URL=http://host:port или socks5://host:port — необязательная настройка.
"""

import asyncio
import html
import logging
import os
import sys
from pathlib import Path

from aiohttp import ClientConnectorError
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

from данные.questions import QUESTIONS, calculate_result
from хранилище.заявки import save_application

try:
    # Ошибки прокси приходят из python-socks (ставится вместе с aiohttp-socks).
    from python_socks import ProxyError
except ImportError:  # пакета нет — прокси не используется, ловить нечего
    ProxyError = None

# Сетевые сбои: к Telegram напрямую и через прокси.
NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ClientConnectorError,
    TelegramNetworkError,
)
if ProxyError is not None:
    NETWORK_ERRORS += (ProxyError,)

# Папка проекта: картинки вопросов лежат в Медиа/ рядом с bot.py.
BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "Медиа"

# Канал проекта: кнопка-ссылка стоит под результатом и под благодарностью.
# Заглушка: заменить на реальный адрес канала, когда он появится.
CHANNEL_URL = "https://t.me/zerocoder_channel"

# --- Тексты сообщений --------------------------------------------------------
# Служебные тексты живут в коде, вопросы и описания стилей — в пакете данные.

WELCOME_TEXT = (
    "Здравствуйте! Это бот «Определи свой стиль интерьера».\n\n"
    "За 7 коротких вопросов он подскажет, какой стиль вам ближе: "
    "скандинавский, лофт, минимализм, эклектика или классика.\n\n"
    "Тест занимает 2-3 минуты. Результат покажу сразу после последнего "
    "ответа."
)

INSTRUCTION_TEXT = (
    "Тест состоит из 7 вопросов. Выберите вариант, который вам ближе.\n\n"
    "Правильных и неправильных ответов нет: важно не то, как «принято», "
    "а то, в каком интерьере вам действительно хотелось бы жить.\n\n"
    "В конце покажу ваш стиль, расскажу о нём и подскажу, с чего начать."
)

# Пояснение в результате, если стилей оказалось два.
TIE_NOTE = "Баллы разделились поровну, поэтому подходят оба стиля."

# Ответ на нажатие кнопки вопроса, который уже сменился.
STALE_ANSWER_TEXT = "Этот вопрос уже неактуален"

# --- Тексты формы заявки -----------------------------------------------------
ASK_NAME_TEXT = "Как вас зовут?"
NAME_ERROR_TEXT = (
    "Подскажите имя от 2 до 64 символов: одним словом или именем "
    "и фамилией, без лишних знаков."
)

ASK_TIME_FORMAT_TEXT = (
    "Когда вам удобно созвониться и как удобнее общаться? "
    "(Например: завтра после 18:00, Telegram)"
)
TIME_FORMAT_ERROR_TEXT = (
    "Уточните время и формат связи: от 3 до 100 символов. "
    "Например: «завтра после 18:00, Telegram»."
)

ASK_CONTACT_TEXT = "Оставьте ваш контакт (телефон или username)"
CONTACT_ERROR_TEXT = (
    "Контакт не сохранился: нужно от 3 до 100 символов. "
    "Пришлите телефон или ник в Telegram."
)

THANKS_TEXT = (
    "Спасибо! Мы свяжемся с вами в ближайшее время. "
    "Ваш стиль: {style}. "
    "(Нажимая «Отправить», вы согласились на обработку "
    "персональных данных)"
)
SAVE_ERROR_TEXT = (
    "Не удалось сохранить заявку: что-то сломалось на нашей стороне. "
    "Отправьте контакт ещё раз или напишите позже."
)


# --- Состояния ---------------------------------------------------------------
class TestStates(StatesGroup):
    """Прохождение теста: ждём ответ на текущий вопрос."""

    answering = State()


class RequestStates(StatesGroup):
    """Заявка на вводную сессию: имя, время и формат связи, контакт."""

    waiting_name = State()
    waiting_time_format = State()
    waiting_contact = State()


# --- Клавиатуры --------------------------------------------------------------
def start_keyboard() -> InlineKeyboardMarkup:
    """Кнопка под приветствием: вход в тест."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Начать тест",
                    callback_data="start_test",
                )
            ]
        ]
    )


def go_keyboard() -> InlineKeyboardMarkup:
    """Кнопка под инструкцией: переход к вопросам."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поехали", callback_data="test_go")]
        ]
    )


def answers_keyboard(question: dict) -> InlineKeyboardMarkup:
    """Кнопки вариантов ответа: callback_data = ans_<номер варианта>.

    Номер варианта берём из порядка в QUESTIONS: по нему обработчик
    находит веса ответа в данных.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=option["text"],
                callback_data=f"ans_{number}",
            )
        ]
        for number, option in enumerate(question["options"])
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_button() -> InlineKeyboardButton:
    """Кнопка-ссылка на Telegram-канал проекта."""
    return InlineKeyboardButton(
        text="Перейти в Telegram-канал",
        url=CHANNEL_URL,
    )


def result_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под результатом: заявка, канал и новый тест."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оставить заявку",
                    callback_data="book_consult",
                )
            ],
            [channel_button()],
            [
                InlineKeyboardButton(
                    text="Пройти ещё раз",
                    callback_data="restart",
                )
            ],
        ]
    )


def thanks_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под благодарностью: канал и возврат в начало."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [channel_button()],
            [
                InlineKeyboardButton(
                    text="В начало",
                    callback_data="restart",
                )
            ],
        ]
    )


# --- Тексты экранов ----------------------------------------------------------
def question_text(index: int, question: dict) -> str:
    """Экран вопроса: счётчик «Вопрос N из 7» и сам вопрос."""
    return (
        f"<b>Вопрос {index + 1} из {len(QUESTIONS)}</b>\n\n"
        f"{question['text']}"
    )


def format_result(result: dict) -> str:
    """Итоговый экран: стиль, описание и совет «с чего начать».

    При ничьей показываем оба стиля и готовый совет, как их объединить.
    Если победителей три, третьего называем отдельной строкой: в
    tie_styles лежат все, и терять его нельзя.
    """
    primary = result["primary"]
    if not result["is_tie"]:
        return (
            f"<b>Ваш стиль — {primary['title']}</b>\n\n"
            f"{primary['description']}\n\n"
            f"<b>С чего начать</b>\n{primary['advice']}"
        )

    secondary = result["secondary"]
    text = (
        f"<b>Вы между «{primary['title']}» и «{secondary['title']}»</b>\n\n"
        f"{TIE_NOTE}\n\n"
        f"<b>{primary['title']}</b>\n{primary['description']}\n\n"
        f"<b>{secondary['title']}</b>\n{secondary['description']}\n\n"
        f"<b>Как объединить</b>\n{result['combine_advice']}"
    )
    others = result["tie_styles"][2:]
    if others:
        names = ", ".join(style["title"] for style in others)
        text += f"\n\nБлизок также стиль: {names}."
    return text


def result_style_value(result: dict):
    """Стиль для заявки: id победителя или список id при ничьей."""
    if result["is_tie"]:
        return [style["id"] for style in result["tie_styles"]]
    return result["primary"]["id"]


def result_style_title(result: dict) -> str:
    """Название стиля для сообщений: «Лофт» или «Лофт и Минимализм»."""
    if result["is_tie"]:
        names = [style["title"] for style in result["tie_styles"][:2]]
        return " и ".join(names)
    return result["primary"]["title"]


# --- Вспомогательные функции -------------------------------------------------
async def safe_delete(message: Message) -> None:
    """Удаляет сообщение и не падает, если оно уже удалено.

    В чате должно оставаться одно актуальное сообщение с кнопками:
    иначе пользователь нажимает кнопку со старого экрана, а бот
    реагирует неожиданно.
    """
    try:
        await message.delete()
    except TelegramBadRequest:
        # Сообщение старше 48 часов или уже удалено — это не ошибка.
        pass


# --- Обработчики -------------------------------------------------------------
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Команда /start: приветствие и кнопка входа в тест.

    Единственная реакция на /start: сначала сбрасываем состояние
    (state.clear() снимает и тест, и любой шаг формы заявки), затем
    показываем приветствие с кнопкой «Начать тест». Функций формы
    заявки (RequestStates) здесь не вызывается: /start никогда не
    открывает форму сам.
    """
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=start_keyboard())


async def cmd_reset(message: Message, state: FSMContext) -> None:
    """Команда /reset: сбрасывает любой шаг и запускает /start заново.

    Нужна, если пользователь застрял в форме заявки или тесте: команда
    работает из любого состояния, в отличие от кнопок экрана.
    """
    # Отдельной логики у /reset нет: он делает то же, что /start, —
    # очищает состояние и показывает приветствие с кнопкой входа в тест.
    await cmd_start(message, state)


async def exit_form_on_command(
    message: Message, state: FSMContext
) -> bool:
    """True, если вместо данных пришла команда: форму прерываем.

    Проверка стоит первой строкой в каждом шаге заявки. Имя, время
    и контакт — это свободный текст, поэтому без проверки «/start»
    мог бы уйти в поле имени. Команда важнее формы: состояние
    сбрасывается, пользователь возвращается в начало.
    """
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return False

    # Та же логика, что у /start: очистка состояния и приветствие.
    await cmd_start(message, state)
    return True


async def on_start_test(callback: CallbackQuery, state: FSMContext) -> None:
    """Нажатие «Начать тест»: инструкция и кнопка «Поехали»."""
    # callback.message может быть None, если сообщение удалено, — проверяем.
    if callback.message is None:
        await callback.answer("Сообщение устарело, отправьте /start")
        return

    await state.clear()
    await safe_delete(callback.message)
    await callback.message.answer(
        INSTRUCTION_TEXT,
        reply_markup=go_keyboard(),
    )
    # Убираем «часики» на кнопке, иначе Telegram крутит их 30 секунд.
    await callback.answer()


async def on_test_go(callback: CallbackQuery, state: FSMContext) -> None:
    """«Поехали»: начинаем тест с первого вопроса."""
    if callback.message is None:
        await callback.answer("Сообщение устарело, отправьте /start")
        return

    await state.set_state(TestStates.answering)
    await state.set_data({"index": 0, "answers": [], "message_id": None})
    await safe_delete(callback.message)
    await send_question(callback.message, state)
    await callback.answer()


async def send_question(message: Message, state: FSMContext) -> None:
    """Отправляет текущий вопрос: фото с подписью или только текст.

    Картинки лежат в Медиа/ и называются вопрос_N.jpg. Если файла нет
    или Telegram не принял фото, вопрос уходит текстом: тест
    продолжается, бот не падает.
    """
    data = await state.get_data()
    index = data.get("index", 0)
    question = QUESTIONS[index]
    text = question_text(index, question)
    keyboard = answers_keyboard(question)
    image_path = MEDIA_DIR / question["image"]

    sent = None
    if image_path.is_file():
        try:
            sent = await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=text,
                reply_markup=keyboard,
            )
        except (FileNotFoundError, TelegramBadRequest) as error:
            logging.warning(
                "Картинка %s не отправлена: %s", image_path.name, error
            )
    if sent is None:
        sent = await message.answer(text, reply_markup=keyboard)

    # id нужен, чтобы отличить кнопки актуального вопроса от старых.
    await state.update_data(message_id=sent.message_id)


async def on_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Ответ на вопрос: следующий вопрос или итог теста."""
    if callback.message is None:
        await callback.answer("Сообщение устарело, отправьте /start")
        return

    if await state.get_state() != TestStates.answering.state:
        await callback.answer(
            "Тест ещё не начат: нажмите «Начать тест»",
            show_alert=True,
        )
        return

    data = await state.get_data()
    # Кнопка со старого экрана: её message_id уже не совпадает с текущим.
    if callback.message.message_id != data.get("message_id"):
        await callback.answer(STALE_ANSWER_TEXT)
        return

    index = data.get("index", 0)
    answers = list(data.get("answers") or [])
    options = QUESTIONS[index]["options"]

    try:
        variant = int(callback.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Не поняла ответ, выберите вариант заново")
        return

    if not 0 <= variant < len(options):
        await callback.answer("Такого варианта нет, выберите заново")
        return

    # Веса копируем: словарь вопросов общий для всех пользователей.
    # Формат как в calculate_result: {"scores": {"loft": 2}}.
    answers.append({"scores": dict(options[variant]["scores"])})
    next_index = index + 1

    await callback.answer()
    await safe_delete(callback.message)

    if next_index < len(QUESTIONS):
        await state.update_data(
            index=next_index,
            answers=answers,
            message_id=None,
        )
        await send_question(callback.message, state)
        return

    result = calculate_result(answers)
    # Тест закончен: состояние прохождения снимаем, но стиль оставляем
    # в FSM — его возьмёт заявка на вводную сессию.
    await state.set_state(None)
    await state.set_data(
        {
            "result_style": result_style_value(result),
            "result_title": result_style_title(result),
        }
    )
    await callback.message.answer(
        format_result(result),
        reply_markup=result_keyboard(),
    )


async def on_restart(callback: CallbackQuery, state: FSMContext) -> None:
    """«Пройти ещё раз» и «В начало»: сброс и экран приветствия."""
    if callback.message is None:
        await callback.answer("Сообщение устарело, отправьте /start")
        return

    await state.clear()
    await safe_delete(callback.message)
    await callback.message.answer(
        WELCOME_TEXT,
        reply_markup=start_keyboard(),
    )
    await callback.answer()


# --- Заявка на вводную сессию ------------------------------------------------
async def on_book_consult(callback: CallbackQuery, state: FSMContext) -> None:
    """«Оставить заявку»: начинаем форму с вопроса об имени."""
    if callback.message is None:
        await callback.answer("Сообщение устарело, отправьте /start")
        return

    data = await state.get_data()
    style = data.get("result_style")
    if not style:
        # Кнопка со старого экрана или тест не пройден: стиля нет,
        # записывать в заявку нечего.
        await callback.answer(
            "Сначала пройдите тест: нажмите /start",
            show_alert=True,
        )
        return

    # Стиль явно переписываем в данные заявки: set_state данные не чистит,
    # но так стиль точно останется в FSM до последнего шага формы.
    await state.update_data(
        result_style=style,
        result_title=data.get("result_title", ""),
    )
    await state.set_state(RequestStates.waiting_name)
    await safe_delete(callback.message)
    await callback.message.answer(ASK_NAME_TEXT)
    await callback.answer()


async def on_request_name(message: Message, state: FSMContext) -> None:
    """Имя из заявки: проверяем длину и спрашиваем время и формат связи."""
    # /start и другие команды не должны попадать в поле имени.
    if await exit_form_on_command(message, state):
        return

    name = (message.text or "").strip()
    if not 2 <= len(name) <= 64:
        await message.answer(NAME_ERROR_TEXT)
        return

    # В FSM имя хранится как есть: экранирование нужно только при выводе
    # в HTML, а в заявку имя уходит в исходном виде.
    await state.update_data(request_name=name)
    await state.set_state(RequestStates.waiting_time_format)
    await message.answer(ASK_TIME_FORMAT_TEXT)


async def on_request_time_format(message: Message, state: FSMContext) -> None:
    """Время и формат связи: проверяем длину и спрашиваем контакт."""
    # Команда посреди формы возвращает в начало, а не пишется в поле.
    if await exit_form_on_command(message, state):
        return

    time_format = (message.text or "").strip()
    if not 3 <= len(time_format) <= 100:
        await message.answer(TIME_FORMAT_ERROR_TEXT)
        return

    await state.update_data(request_time_format=time_format)
    await state.set_state(RequestStates.waiting_contact)
    await message.answer(ASK_CONTACT_TEXT)


async def on_request_contact(message: Message, state: FSMContext) -> None:
    """Контакт: последний шаг формы, сохраняем заявку в JSON."""
    # На последнем шаге команда тоже прерывает форму без записи заявки.
    if await exit_form_on_command(message, state):
        return

    contact = (message.text or "").strip()
    if not 3 <= len(contact) <= 100:
        await message.answer(CONTACT_ERROR_TEXT)
        return

    data = await state.get_data()
    try:
        save_application(
            user_id=message.from_user.id,
            name=data.get("request_name", ""),
            time_format=data.get("request_time_format", ""),
            contact=contact,
            style=data.get("result_style"),
        )
    except OSError:
        # Файл недоступен (нет прав, диск занят). Состояние не снимаем:
        # пользователь может отправить контакт ещё раз.
        logging.exception("Не удалось сохранить заявку")
        await message.answer(SAVE_ERROR_TEXT)
        return

    await state.clear()
    await message.answer(
        THANKS_TEXT.format(style=html.escape(data.get("result_title", ""))),
        reply_markup=thanks_keyboard(),
    )


# --- Сборка приложения -------------------------------------------------------
def register_handlers(dp: Dispatcher) -> None:
    """Подключает обработчики к диспетчеру.

    Порядок важен: /start и /reset стоят первыми и сбрасывают любое
    состояние, поэтому заполнение заявки можно прервать командой.
    Текстовые шаги заявки ловятся только в своих состояниях.
    """
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_reset, Command("reset"))
    dp.callback_query.register(on_start_test, F.data == "start_test")
    dp.callback_query.register(on_test_go, F.data == "test_go")
    dp.callback_query.register(on_restart, F.data == "restart")
    dp.callback_query.register(on_book_consult, F.data == "book_consult")
    dp.callback_query.register(on_answer, F.data.startswith("ans_"))
    dp.message.register(
        on_request_name,
        StateFilter(RequestStates.waiting_name),
    )
    dp.message.register(
        on_request_time_format,
        StateFilter(RequestStates.waiting_time_format),
    )
    dp.message.register(
        on_request_contact,
        StateFilter(RequestStates.waiting_contact),
    )


def read_token() -> str:
    """Читает токен из окружения.

    Токен хранится только в .env — в коде и в репозитории его быть
    не должно.
    """
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "ваш_токен_здесь":
        raise RuntimeError(
            "Не найден BOT_TOKEN. Скопируйте .env.example в .env "
            "и вставьте токен, который выдал @BotFather."
        )
    return token


def read_proxy() -> str | None:
    """Прокси из .env: пустое значение — подключаемся к Telegram напрямую.

    PROXY_URL — необязательная настройка. Пустая строка, пробелы и вовсе
    отсутствующая переменная означают «прокси не нужен».
    """
    proxy = os.getenv("PROXY_URL", "").strip()
    return proxy or None


def create_session() -> AiohttpSession:
    """Сессия для Telegram: через прокси, если он указан в .env.

    Сам URL прокси в лог не пишем: в нём бывают логин и пароль.
    Без прокси возвращается обычная сессия — бот работает как раньше.
    """
    proxy = read_proxy()
    if proxy is None:
        logging.info("Прокси не задан: подключаюсь к Telegram напрямую")
        return AiohttpSession()

    try:
        session = AiohttpSession(proxy=proxy)
    except RuntimeError as error:
        # aiogram так сообщает, что для прокси не хватает aiohttp-socks.
        raise RuntimeError(
            "PROXY_URL задан, но пакет aiohttp-socks не установлен. "
            "Выполните: pip install -r requirements.txt"
        ) from error

    logging.info("Прокси включён: запросы к Telegram идут через него")
    return session


def network_error_message(error: Exception) -> str:
    """Подсказка при сетевой ошибке: что проверить и куда вписать прокси."""
    if ProxyError is not None and isinstance(error, ProxyError):
        return (
            f"Бот не запущен: не удалось подключиться через прокси "
            f"({error.__class__.__name__}).\n"
            "Проверьте в PROXY_URL (.env) адрес и порт прокси, логин "
            "и пароль, а также запущен ли сервис, который его выдал. "
            "Если прокси не нужен, очистите PROXY_URL: бот пойдёт напрямую."
        )

    return (
        f"Бот не запущен: нет связи с api.telegram.org "
        f"({error.__class__.__name__}).\n"
        "Проверьте интернет и VPN. Если Telegram недоступен напрямую, "
        "впишите прокси в .env, например:\n"
        "PROXY_URL=http://host:port или PROXY_URL=socks5://host:port"
    )


async def main() -> None:
    """Настраивает бота и запускает получение сообщений (long polling)."""
    # Токен читаем строго из .env рядом с bot.py: без явного пути
    # python-dotenv поднимается по дереву папок и может взять токен
    # из соседнего проекта.
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = Bot(
        token=read_token(),
        session=create_session(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # MemoryStorage держит состояние прохождения в памяти: при перезапуске
    # незаконченные тесты сбрасываются, для учебного проекта этого хватает.
    dp = Dispatcher(storage=MemoryStorage())

    register_handlers(dp)

    # Снимаем старый вебхук, чтобы polling не конфликтовал с ним.
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as error:
        # Понятное сообщение вместо трейсбека, если не заполнен .env
        # или для прокси не хватает aiohttp-socks.
        print(f"Бот не запущен: {error}")
        sys.exit(1)
    except NETWORK_ERRORS as error:
        # Сеть не пускает к api.telegram.org или не отвечает прокси:
        # это не ошибка кода, а ограничение доступа.
        print(network_error_message(error))
        sys.exit(1)
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")
