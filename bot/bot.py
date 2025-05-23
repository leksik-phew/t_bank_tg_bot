import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os
import asyncio

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Настройка логирования (аналогично bot.py)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Хранилище для периодичности отправки (в минутах и человекочитаемое описание)
chat_schedules = {}

# Константы для состояний
STATE_WAITING_MINUTES = "waiting_minutes"
STATE_WAITING_DAYS = "waiting_days"

# Функция для создания клавиатуры периодичности


def get_periodicity_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Каждый час", callback_data="period_hourly"),
            InlineKeyboardButton("Ежедневно", callback_data="period_daily"),
        ],
        [
            InlineKeyboardButton("Еженедельно", callback_data="period_weekly"),
        ],
        [
            InlineKeyboardButton(
                "Свои минуты", callback_data="period_custom_minutes"),
            InlineKeyboardButton(
                "Свои дни", callback_data="period_custom_days"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# Команда /start


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    logger.info(f"Команда /start вызвана в чате {chat_id}")
    await update.message.reply_text(
        "Привет! Я бот, который отправляет сводки экономических новостей. "
        "Используйте /set, чтобы выбрать периодичность отправки новостей через кнопки. "
        "Для списка команд используйте /help."
    )

# Команда /help


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    logger.info(f"Команда /help вызвана в чате {chat_id}")
    await update.message.reply_text(
        "Доступные команды:\n"
        "- /start: Запустите бота и получите приветственное сообщение.\n"
        "- /set: Настройте периодичность отправки новостей с помощью кнопок.\n"
        "- /help: Просмотрите список доступных команд."
    )

# Команда /set для выбора периодичности


async def set_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    logger.info(f"Команда /set вызвана в чате {chat_id}")
    await update.message.reply_text(
        "Выберите периодичность отправки новостей:",
        reply_markup=get_periodicity_keyboard()
    )

# Обработка выбора кнопок


async def handle_periodicity_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    callback_data = query.data
    logger.info(f"Выбор периодичности в чате {chat_id}: {callback_data}")

    # Предустановленные периодичности с человекочитаемыми описаниями
    periodicity_map = {
        "period_hourly": (60, "час"),
        "period_daily": (24 * 60, "день"),
        "period_weekly": (7 * 24 * 60, "неделя"),
    }

    if callback_data in periodicity_map:
        minutes, display_text = periodicity_map[callback_data]
        chat_schedules[chat_id] = {"minutes": minutes, "display": display_text}
        await query.message.reply_text(f"Установлена периодичность: каждый {display_text}.")

        # Удаление предыдущей задачи, если она была
        if chat_id in context.chat_data:
            context.chat_data[chat_id].cancel()

        # Запуск новой задачи
        job = context.job_queue.run_repeating(
            send_news_summary,
            interval=minutes * 60,
            first=0,
            data=chat_id
        )
        context.chat_data[chat_id] = job
        logger.info(
            f"Установлено расписание для чата {chat_id}: каждый {display_text}")

    elif callback_data == "period_custom_minutes":
        context.user_data["state"] = STATE_WAITING_MINUTES
        await query.message.reply_text("Пожалуйста, введите количество минут (например, 30):")
        logger.info(f"Ожидается ввод минут в чате {chat_id}")
    elif callback_data == "period_custom_days":
        context.user_data["state"] = STATE_WAITING_DAYS
        await query.message.reply_text("Пожалуйста, введите количество дней (например, 2):")
        logger.info(f"Ожидается ввод дней в чате {chat_id}")

# Обработка пользовательского ввода (минуты или дни)


async def handle_custom_periodicity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text

    # Проверка, что ввод - число
    if not text.isdigit():
        await update.message.reply_text("Пожалуйста, введите только число.")
        logger.warning(f"Некорректный ввод в чате {chat_id}: {text}")
        return

    value = int(text)
    state = context.user_data.get("state")

    if state == STATE_WAITING_MINUTES:
        minutes = value
        if minutes <= 0:
            await update.message.reply_text("Число минут должно быть больше 0.")
            logger.warning(
                f"Некорректное число минут в чате {chat_id}: {value}")
            return
        display_text = f"{value} {'минута' if value == 1 else 'минуты' if 2 <= value % 10 <= 4 and (value % 100 < 10 or value % 100 > 20) else 'минут'}"
    elif state == STATE_WAITING_DAYS:
        minutes = value * 24 * 60  # Преобразуем дни в минуты
        if minutes <= 0:
            await update.message.reply_text("Число дней должно быть больше 0.")
            logger.warning(
                f"Некорректное число дней в чате {chat_id}: {value}")
            return
        display_text = f"{value} {'день' if value == 1 else 'дня' if 2 <= value % 10 <= 4 and (value % 100 < 10 or value % 100 > 20) else 'дней'}"
    else:
        await update.message.reply_text("Ошибка: состояние не определено. Используйте /set для выбора периодичности.")
        logger.error(f"Неизвестное состояние в чате {chat_id}")
        return

    chat_schedules[chat_id] = {"minutes": minutes, "display": display_text}
    await update.message.reply_text(f"Установлена периодичность: каждые {display_text}.")

    # Удаление предыдущей задачи, если она была
    if chat_id in context.chat_data:
        context.chat_data[chat_id].cancel()

    # Запуск новой задачи
    job = context.job_queue.run_repeating(
        send_news_summary,
        interval=minutes * 60,
        first=0,
        data=chat_id
    )
    context.chat_data[chat_id] = job
    logger.info(
        f"Установлено расписание для чата {chat_id}: каждые {display_text}")

    # Сброс состояния
    context.user_data.pop("state", None)

# Функция отправки сводки новостей


async def send_news_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    # Заглушка для сбора и суммаризации новостей
    news_summary = get_news_summary()
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=news_summary
        )
        logger.info(f"Отправлена сводка новостей в чат {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")

# Заглушка для функции сбора и суммаризации новостей


def get_news_summary() -> str:
    # TODO: Реализовать сбор новостей из Telegram-каналов и их суммаризацию
    # Импорт модели
    # Запрос контента от модели
    # Вывод в соо
    return (
        "Экономические новости:\n"
        "1. Заглушка: Рост ВВП США на 2.5% в Q1 2025.\n"
        "2. Заглушка: Цены на нефть Brent достигли $80 за баррель.\n"
        "3. Заглушка: ЕЦБ сохранил ставку на уровне 3.5%."
    )


def main() -> None:
    # Проверка наличия токена
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN не найден в .env файле")
        return

    # Инициализация бота
    application = Application.builder().token(BOT_TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("set", set_schedule))
    application.add_handler(CallbackQueryHandler(handle_periodicity_choice))
    application.add_handler(MessageHandler(
        filters.Regex(r'^\d+$'), handle_custom_periodicity))

    # Запуск бота
    logger.info("----------------------- Бот запущен -----------------------")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
