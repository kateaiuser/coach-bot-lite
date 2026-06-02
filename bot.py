"""Coach Bot Lite — минимальный Telegram-коуч на Claude, без базы данных.

История диалога живёт в памяти процесса (сбрасывается при перезапуске).
Память между перезапусками добавляется позже, см. README.
"""
import os
import logging
from collections import defaultdict, deque

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("coach-bot")

# ---- настройки из переменных окружения ----
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.getenv("MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))
HISTORY = int(os.getenv("HISTORY", "12"))  # сколько последних реплик помнить в сессии

# кому отвечать. пусто = всем (НЕ рекомендуется: чужие будут тратить твой баланс).
_allowed = os.getenv("ALLOWED_USER_IDS", "").replace(" ", "")
ALLOWED_USER_IDS = {int(x) for x in _allowed.split(",") if x} if _allowed else set()


def load_personality() -> str:
    """Личность коуча.

    Приоритет у переменной COACH_PROMPT: её задают приватно в Railway Variables,
    чтобы личное НЕ попадало в публичный репозиторий. Если её нет, берётся файл
    personality.md (это удобно, только когда твой репозиторий приватный),
    иначе мягкая заглушка.
    """
    env_prompt = os.getenv("COACH_PROMPT", "").strip()
    if env_prompt:
        return env_prompt
    try:
        with open("personality.md", encoding="utf-8") as f:
            text = f.read().strip()
            if text:
                return text
    except FileNotFoundError:
        pass
    return (
        "Ты тёплый и честный коуч. Отвечаешь коротко, по делу, без клише. "
        "Подсвечиваешь то, что человек не замечает, и задаёшь один сильный вопрос."
    )


SYSTEM_PROMPT = load_personality()
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# история по чату: chat_id -> очередь сообщений
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY * 2))


def allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text(
        "Привет. Я твой коуч. Напиши, с каким состоянием ты сейчас и что на сегодня."
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        log.warning("Сообщение от чужого user_id=%s, пропускаю", update.effective_user.id)
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text
    history[chat_id].append({"role": "user", "content": user_text})

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        resp = claude.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=list(history[chat_id]),
        )
        answer = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not answer:
            answer = "Я тебя услышала, но не нашла слов. Скажи иначе?"
    except Exception:
        log.exception("Ошибка при запросе к Claude")
        answer = "Что-то пошло не так на моей стороне. Попробуй ещё раз через минуту."
        history[chat_id].pop()  # не сохраняем неудачный ход
        await update.message.reply_text(answer)
        return

    history[chat_id].append({"role": "assistant", "content": answer})
    await update.message.reply_text(answer)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Coach Bot Lite запущен (model=%s, history=%s)", MODEL, HISTORY)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
