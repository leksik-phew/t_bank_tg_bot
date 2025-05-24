import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.ext import JobQueue
from telegram.error import Forbidden
from dotenv import load_dotenv
import os
import asyncio
import sys
from uuid import uuid4
from transformers import GPT2Tokenizer, T5ForConditionalGeneration
import torch
import sqlite3
from datetime import datetime, timedelta

# Добавляем родительскую директорию в системный путь для импорта модуля базы данных
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database.creator import update_db


# Инициализация модели T5 для суммаризации новостей
tokenizer = GPT2Tokenizer.from_pretrained(
    'RussianNLP/FRED-T5-Summarizer', eos_token='</s>')
model = T5ForConditionalGeneration.from_pretrained(
    'RussianNLP/FRED-T5-Summarizer')
device = 'cpu'
model.to(device)

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Хранилище расписаний чатов (периодичность в минутах и текст для отображения)
chat_schedules = {}

# Константы для состояний ввода пользователя
STATE_WAITING_MINUTES = "waiting_minutes"
STATE_WAITING_DAYS = "waiting_days"


def get_periodicity_keyboard() -> InlineKeyboardMarkup:
    """Создает инлайн-клавиатуру для выбора периодичности отправки новостей."""
    keyboard = [
        [
            InlineKeyboardButton("Каждый час", callback_data="period_hourly"),
            InlineKeyboardButton("Ежедневно", callback_data="period_daily"),
        ],
        [InlineKeyboardButton("Еженедельно", callback_data="period_weekly")],
        [
            InlineKeyboardButton(
                "Свои минуты", callback_data="period_custom_minutes"),
            InlineKeyboardButton(
                "Свои дни", callback_data="period_custom_days"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def is_group_or_channel(update: Update) -> bool:
    """Проверяет, является ли чат группой или каналом."""
    return update.effective_chat.type in ["group", "supergroup", "channel"]


async def check_bot_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет права бота и пользователя в группе или канале."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    # Для личных чатов права не нужны
    if chat_type == "private":
        return True

    # Проверяем, является ли бот администратором
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status not in ["administrator", "creator"]:
            await update.message.reply_text(
                "Ошибка: Бот должен быть администратором в группе или канале для выполнения этой команды."
            )
            logger.warning(f"Бот не является администратором в чате {chat_id}")
            return False
    except Forbidden:
        await update.message.reply_text(
            "Ошибка: Боту не хватает прав для проверки статуса. Пожалуйста, добавьте бота как администратора."
        )
        logger.error(f"Ошибка доступа в чате {chat_id}")
        return False

    # Проверяем, является ли пользователь администратором в группах или супергруппах
    if chat_type in ["group", "supergroup"]:
        try:
            user_member = await context.bot.get_chat_member(chat_id, user_id)
            if user_member.status not in ["administrator", "creator"]:
                await update.message.reply_text(
                    "Ошибка: Только администраторы группы могут использовать команды бота."
                )
                logger.warning(
                    f"Пользователь {user_id} не является администратором в чате {chat_id}")
                return False
        except Forbidden:
            await update.message.reply_text(
                "Ошибка: Боту не хватает прав для проверки вашего статуса. Пожалуйста, убедитесь, что бот является администратором."
            )
            logger.error(
                f"Ошибка проверки статуса пользователя {user_id} в чате {chat_id}")
            return False

    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start для инициализации бота."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    logger.info(f"Команда /start вызвана в чате {chat_id} (тип: {chat_type})")

    if not await check_bot_permissions(update, context):
        return

    welcome_message = (
        "Привет! Я бот, который отправляет сводки экономических новостей. "
        "Используйте /set, чтобы выбрать периодичность отправки новостей через кнопки. "
        "Для списка команд используйте /help."
    )
    if chat_type == "channel":
        welcome_message += "\n\nУбедитесь, что бот имеет права на отправку сообщений в канал."
    elif chat_type in ["group", "supergroup"]:
        welcome_message += "\n\nВ группах команды доступны только администраторам."

    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /help для отображения списка команд."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    logger.info(f"Команда /help вызвана в чате {chat_id} (тип: {chat_type})")

    if not await check_bot_permissions(update, context):
        return

    await update.message.reply_text(
        "Доступные команды:\n"
        "- /start: Запустите бота и получите приветственное сообщение.\n"
        "- /set: Настройте периодичность отправки новостей с помощью кнопок.\n"
        "- /stop: Остановить отправку новостей в этом чате.\n"
        "- /help: Просмотрите список доступных команд."
    )


async def set_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /set для настройки периодичности отправки новостей."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    logger.info(f"Команда /set вызвана в чате {chat_id} (тип: {chat_type})")

    if not await check_bot_permissions(update, context):
        return

    await update.message.reply_text(
        "Выберите периодичность отправки новостей:",
        reply_markup=get_periodicity_keyboard()
    )


async def handle_periodicity_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает выбор периодичности через инлайн-клавиатуру."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    message_id = query.message.message_id  # ID сообщения с клавиатурой
    callback_data = query.data
    logger.info(f"Выбор периодичности в чате {chat_id}: {callback_data}")

    periodicity_map = {
        "period_hourly": (60, "час"),
        "period_daily": (24 * 60, "день"),
        "period_weekly": (7 * 24 * 60, "неделя"),
    }

    if callback_data in periodicity_map:
        minutes, display_text = periodicity_map[callback_data]
        chat_schedules[chat_id] = {"minutes": minutes, "display": display_text}
        await query.message.reply_text(f"Установлена периодичность: каждый {display_text}.")

        if chat_id in context.chat_data:
            context.chat_data[chat_id].schedule_removal()

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

    # Удаляем сообщение с клавиатурой
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(
            f"Сообщение с клавиатурой (ID: {message_id}) удалено в чате {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения в чате {chat_id}: {e}")


async def handle_custom_periodicity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает пользовательский ввод периодичности (минуты или дни)."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    text = update.message.text

    if not await check_bot_permissions(update, context):
        return

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
        minutes = value * 24 * 60
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

    if chat_id in context.chat_data:
        context.chat_data[chat_id].schedule_removal()

    job = context.job_queue.run_repeating(
        send_news_summary,
        interval=minutes * 60 - 30,
        first=0,
        data=chat_id
    )
    context.chat_data[chat_id] = job
    logger.info(
        f"Установлено расписание для чата {chat_id}: каждые {display_text}")

    context.user_data.pop("state", None)


async def send_news_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сводку новостей в указанный чат."""
    chat_id = context.job.data
    news_summary = get_news_summary()
    try:
        await context.bot.send_message(chat_id=chat_id, text=news_summary)
        logger.info(f"Отправлена сводка новостей в чат {chat_id}")
    except Forbidden:
        logger.error(
            f"Ошибка: Бот не имеет прав для отправки сообщений в чат {chat_id}")
        if chat_id in context.chat_data:
            context.chat_data[chat_id].schedule_removal()
            del context.chat_data[chat_id]
            del chat_schedules[chat_id]
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения в чат {chat_id}: {e}")


def get_summary(prompt) -> str:
    """Генерирует сводку новостей с помощью модели T5."""
    input_ids = tokenizer.encode(prompt, return_tensors="pt").to(device)
    outputs = model.generate(
        input_ids,
        max_new_tokens=1000,
        min_new_tokens=50,
        num_beams=5,
        early_stopping=True,
        no_repeat_ngram_size=4,
        top_p=0.9,
        do_sample=True
    )
    return tokenizer.decode(outputs[0], skip_special_tokens=True)


def get_news_summary() -> str:
    """Получает и суммирует последние новости из базы данных."""
    db_path = "database/bee.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        yesterday = datetime.now() - timedelta(days=1)
        cursor.execute(
            """
            SELECT title, content, channel 
            FROM news 
            WHERE pub_date >= ? 
            ORDER BY PUB_DATE DESC
            LIMIT 7
            """,
            (yesterday.strftime('%Y-%m-%d %H:%M:%S'),)
        )
        news_items = cursor.fetchall()
        conn.close()

        if not news_items:
            return "На данный момент нет свежих экономических новостей."

        prompt = (
            "Суммаризируй следующие экономические новости в виде нумерованного списка."
        )
        for title, content, source in news_items:
            prompt += f"Заголовок: {title}\nИсточник: {source}\nТекст: {content[:500]}\n\n"

        summary = get_summary(prompt)
        return f"📈 Экономическая сводка:\n\n{summary}\n\nСписок источников: {', '.join(set(item[2] for item in news_items))}"

    except Exception as e:
        logger.error(f"Ошибка при генерации сводки новостей: {e}")
        return "Произошла ошибка при подготовке экономической сводки. Пожалуйста, попробуйте позже."


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /stop для остановки отправки новостей в текущем чате."""
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    logger.info(f"Команда /stop вызвана в чате {chat_id} (тип: {chat_type})")

    if not await check_bot_permissions(update, context):
        return

    if chat_id not in chat_schedules:
        await update.message.reply_text("В этом чате не настроена отправка новостей.")
        logger.info(
            f"Попытка остановки в чате {chat_id}, но расписание не найдено")
        return

    if chat_id in context.chat_data:
        context.chat_data[chat_id].schedule_removal()
        del context.chat_data[chat_id]
        logger.info(f"Задача отправки новостей для чата {chat_id} удалена")

    del chat_schedules[chat_id]
    logger.info(f"Расписание для чата {chat_id} удалено")
    await update.message.reply_text(
        "Отправка новостных сводок в этом чате остановлена. Используйте /set для настройки нового расписания."
    )


def main() -> None:
    """Инициализирует и запускает бота."""
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN не найден в .env файле")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    global db_job_queue
    db_job_queue = JobQueue()
    db_job_queue.set_application(application)

    db_job_queue.run_once(
        callback=lambda context: update_db(),
        when=0,
        name="initial_db_update"
    )
    db_job_queue.run_repeating(
        callback=lambda context: update_db(),
        interval=600,
        first=600,
        name="periodic_db_update"
    )
    db_job_queue.start()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("set", set_schedule))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CallbackQueryHandler(handle_periodicity_choice))
    application.add_handler(MessageHandler(
        filters.Regex(r'^\d+$'), handle_custom_periodicity))

    logger.info("----------------------- Бот запущен -----------------------")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
