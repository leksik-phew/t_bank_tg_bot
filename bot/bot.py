import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.ext import JobQueue
from dotenv import load_dotenv
import os
import asyncio
import torch

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

path = sys.path[-1]
print(path)

from database.creator import update_db

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
            context.chat_data[chat_id].schedule_removal()

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
        context.chat_data[chat_id].schedule_removal()

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
    from transformers import GPT2Tokenizer, T5ForConditionalGeneration
    import torch
    import sqlite3
    from datetime import datetime, timedelta
    
    # Initialize the summarization model
    tokenizer = GPT2Tokenizer.from_pretrained('RussianNLP/FRED-T5-Summarizer', eos_token='</s>')
    model = T5ForConditionalGeneration.from_pretrained('RussianNLP/FRED-T5-Summarizer')
    device = 'cpu'
    model.to(device)
    
    # Connect to the database
    db_path = "database/bee.db"
    try:
        conn = sqlite3.connect(db_path)

        print(conn)

        cursor = conn.cursor()
        
        # Get news from the last 24 hours
        yesterday = datetime.now() - timedelta(days=1)
        cursor.execute("""
            SELECT title, content, channel 
            FROM news 
            WHERE pub_date >= ? 
            ORDER BY pub_date DESC
            LIMIT 10
        """, (yesterday.strftime('%Y-%m-%d %H:%M:%S'),))
        
        news_items = cursor.fetchall()
        conn.close()
        
        if not news_items:
            return "На сегодня нет свежих экономических новостей."
            
        # Prepare the prompt for summarization
        prompt = "Суммаризируй следующие экономические новости:\n\n"
        for title, content, source in news_items:
            prompt += f"Заголовок: {title}\nИсточник: {source}\nТекст: {content[:500]}\n\n"
        
        # Generate the summary
        input_ids = torch.tensor([tokenizer.encode(prompt)]).to(device)
        outputs = model.generate(
            input_ids,
            eos_token_id=tokenizer.eos_token_id,
            num_beams=5,
            min_new_tokens=50,
            max_new_tokens=500,
            do_sample=True,
            no_repeat_ngram_size=4,
            top_p=0.9
        )
        
        summary = tokenizer.decode(outputs[0][1:])
        return f"📈 Экономическая сводка:\n\n{summary}\n\nИсточники: {', '.join(set(item[2] for item in news_items))}"
        
    except Exception as e:
        logger.error(f"Ошибка при генерации сводки новостей: {e}")
        return "Произошла ошибка при подготовке экономической сводки. Пожалуйста, попробуйте позже."


def main() -> None:
    # Проверка наличия токена
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN не найден в .env файле")
        return

    # Инициализация бота
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .job_queue(JobQueue())  # Явная инициализация
        .build()
    )

    application.job_queue.run_repeating(
        callback=lambda context: update_db(),
        interval=600,
        first=600
    )

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
