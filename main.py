import asyncio
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "7227173658:AAHB7Zj2EUXE5IHSCBBzOi8k2Ql76bLIkaA")
QUIZ_CHAT_ID = os.getenv("CHAT_ID", "-1001234567890")  # group/channel/chat ID

# ---------------------- Commands ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Hello! I am AbhiX Quiz Bot!\nI can send quizzes automatically.")

# Example quiz data
QUIZZES = [
    {
        "question": "What is the capital of France?",
        "options": ["Berlin", "Madrid", "Paris", "Rome"],
        "correct_option_id": 2
    },
    {
        "question": "Which planet is known as the Red Planet?",
        "options": ["Earth", "Mars", "Jupiter", "Saturn"],
        "correct_option_id": 1
    }
]

# ---------------------- Quiz Scheduler ----------------------
async def quiz_scheduler(app):
    """Send a quiz every 60 seconds."""
    while True:
        for quiz in QUIZZES:
            try:
                await app.bot.send_poll(
                    chat_id=QUIZ_CHAT_ID,
                    question=quiz["question"],
                    options=quiz["options"],
                    type="quiz",
                    correct_option_id=quiz["correct_option_id"],
                    is_anonymous=False
                )
                print(f"✅ Sent quiz: {quiz['question']}")
                await asyncio.sleep(60)  # wait before next quiz
            except Exception as e:
                print(f"❌ Error sending quiz: {e}")
        await asyncio.sleep(5)  # short break before restarting loop

# ---------------------- Startup Hook ----------------------
async def on_startup(app):
    app.create_task(quiz_scheduler(app))  # start background quiz loop

# ---------------------- Main ----------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))

    # Run scheduler after bot starts
    app.post_init = on_startup

    print("🚀 AbhiX Quiz Bot is running...")
    app.run_polling()
