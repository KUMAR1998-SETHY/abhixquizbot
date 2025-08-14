import json, os, re, asyncio, random
from datetime import datetime, timedelta, time as dt_time
from telegram import Update, Poll
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    PollAnswerHandler, ContextTypes, filters
)

# ---------------- CONFIG ----------------
TOKEN = "YOUR_BOT_TOKEN"
ADMINS = [850255908, 779677145]   # Admin IDs
DEFAULT_DURATION = 30              # Default quiz duration in seconds
RANDOMIZE_QUESTIONS = True

QUIZ_FILE = "quizzes.json"
STATE = {}
SCORES = {}
USER_FEEDBACK = {}
TIMER_INPUT = {}  # chat_id -> timer in seconds
SCHEDULED_QUIZZES = {}  # chat_id -> list of scheduled quizzes

# ---------------- QUIZ STORAGE ----------------
def load_quizzes():
    if os.path.exists(QUIZ_FILE):
        with open(QUIZ_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_quizzes(data):
    with open(QUIZ_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ---------------- QUESTION PARSER ----------------
def parse_questions(text):
    questions = []
    blocks = re.split(r'\n\s*\n', text.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        q = lines[0]
        opts, ans, exp = [], -1, ""
        for i, line in enumerate(lines[1:]):
            if 'Explanation:' in line:
                exp = line.split("Explanation:")[-1].strip()
                continue
            if '✅' in line or '✔️' in line:
                ans = i
                line = line.replace('✅', '').replace('✔️', '')
            line = re.sub(r"^[A-Da-d]\.\s*", "", line).strip()
            opts.append(line)
        if ans != -1:
            questions.append((q, opts, ans, exp))
    return questions

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to QuizBot!\nUse /createquiz <title> to begin."
    )

async def createquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /createquiz <quiz title>")
        return
    title = " ".join(context.args)
    data = load_quizzes()
    data.setdefault(user, {})[title] = []
    save_quizzes(data)
    context.user_data["current_quiz"] = title
    await update.message.reply_text(
        f"✅ Quiz '{title}' created. Now send questions or upload a .txt file."
    )

async def handle_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if "current_quiz" not in context.user_data:
        await update.message.reply_text("Use /createquiz <title> first.")
        return
    title = context.user_data["current_quiz"]
    content = update.message.text
    qs = parse_questions(content)
    if not qs:
        await update.message.reply_text("No valid questions found.")
        return
    data = load_quizzes()
    data[user][title].extend(qs)
    save_quizzes(data)
    await update.message.reply_text(f"✅ {len(qs)} question(s) added to '{title}'.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if "current_quiz" not in context.user_data:
        await update.message.reply_text("Use /createquiz <title> first.")
        return
    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("Only .txt files supported.")
        return
    file = await doc.get_file()
    path = f"{doc.file_id}.txt"
    await file.download_to_drive(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    os.remove(path)
    qs = parse_questions(content)
    data = load_quizzes()
    title = context.user_data["current_quiz"]
    data[user][title].extend(qs)
    save_quizzes(data)
    await update.message.reply_text(f"✅ {len(qs)} question(s) added to '{title}'.")

async def myquizzes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    data = load_quizzes()
    if user not in data:
        await update.message.reply_text("You have no quizzes.")
        return
    await update.message.reply_text("\n".join(data[user].keys()))

async def deletequiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /deletequiz <title>")
        return
    title = " ".join(context.args)
    data = load_quizzes()
    if user in data and title in data[user]:
        del data[user][title]
        save_quizzes(data)
        await update.message.reply_text(f"🗑️ Deleted quiz '{title}'.")
    else:
        await update.message.reply_text("Quiz not found.")

# ---------------- HOSTING QUIZ ----------------
async def hostquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /hostquiz <title>")
        return
    title = " ".join(context.args)
    data = load_quizzes()
    if user not in data or title not in data[user] or not data[user][title]:
        await update.message.reply_text("Quiz not found or has no questions.")
        return
    chat_id = update.effective_chat.id
    STATE[chat_id] = {
        "questions": data[user][title],
        "current": 0,
        "message_ids": [],
        "running": True
    }
    SCORES[chat_id] = {}
    await send_next_question(chat_id, context, TIMER_INPUT.get(chat_id, DEFAULT_DURATION))

async def stopquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in STATE:
        STATE[chat_id]["running"] = False
        await update.message.reply_text("🛑 Quiz stopped.")
    else:
        await update.message.reply_text("No active quiz to stop.")

async def send_next_question(chat_id, context, open_period=None):
    state = STATE.get(chat_id)
    if not state or not state["running"]:
        return
    if state["current"] >= len(state["questions"]):
        await context.bot.send_message(chat_id, "✅ Quiz Finished!")
        STATE.pop(chat_id)
        return
    q, opts, ans_idx, explanation = state["questions"][state["current"]]
    poll = await context.bot.send_poll(
        chat_id=chat_id,
        question=q,
        options=opts,
        type=Poll.QUIZ,
        correct_option_id=ans_idx,
        explanation=explanation,
        is_anonymous=False,
        open_period=open_period or DEFAULT_DURATION
    )
    state["message_ids"].append(poll.message_id)
    state["current"] += 1
    # Schedule next question automatically after time expires
    await asyncio.sleep(open_period or DEFAULT_DURATION)
    await send_next_question(chat_id, context, open_period)

# ---------------- POLL HANDLER ----------------
async def poll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.poll_answer.user.id
    for cid in list(STATE.keys()):
        if STATE[cid]["running"]:
            # Scores handling here
            SCORES.setdefault(cid, {})
            user_id = update.poll_answer.user.id
            SCORES[cid].setdefault(user_id, 0)
            # Just placeholder for scoring
            SCORES[cid][user_id] += 1

# ---------------- LEADERBOARD ----------------
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in SCORES:
        await update.message.reply_text("No quiz data available.")
        return
    scores = SCORES[chat_id]
    leaderboard_text = "🏆 Leaderboard:\n"
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    for idx, (user, score) in enumerate(sorted_scores, start=1):
        leaderboard_text += f"{idx}. User {user}: {score} pts\n"
    await update.message.reply_text(leaderboard_text)

# ---------------- TIMER INPUT ----------------
async def handle_timer_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        timer = int(update.message.text.strip())
        TIMER_INPUT[update.effective_chat.id] = timer
        await update.message.reply_text(f"⏱️ Timer set to {timer} seconds for next quiz.")
    except:
        await update.message.reply_text("Send a valid integer for timer in seconds.")

# ---------------- SCHEDULING FEATURES ----------------
async def schedule_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("❌ You are not authorized to schedule quizzes.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /schedule <chat_id> <quiz_title> <HH:MM> [duration_seconds] [repeat]"
        )
        return

    try:
        chat_id = int(context.args[0])
        title = context.args[1]
        time_str = context.args[2]
        duration = int(context.args[3]) if len(context.args) > 3 else DEFAULT_DURATION
        repeat = context.args[4].lower() if len(context.args) > 4 else None
        if repeat not in ("daily", "weekly", None):
            repeat = None
        hour, minute = map(int, time_str.split(":"))
        start_time = datetime.combine(datetime.today(), dt_time(hour, minute))
        SCHEDULED_QUIZZES.setdefault(chat_id, []).append({
            "title": title,
            "start_time": start_time,
            "timer": duration,
            "repeat": repeat
        })
        await update.message.reply_text(
            f"✅ Scheduled quiz '{title}' in chat {chat_id} at {time_str} "
            f"for {duration}s per question. Repeat: {repeat}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def view_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("❌ You are not authorized to view schedules.")
        return
    if not SCHEDULED_QUIZZES:
        await update.message.reply_text("No quizzes are currently scheduled.")
        return
    message = "📅 Scheduled Quizzes:\n\n"
    for chat_id, quizzes in SCHEDULED_QUIZZES.items():
        for quiz in quizzes:
            start_time = quiz["start_time"].strftime("%Y-%m-%d %H:%M")
            duration = quiz.get("timer", DEFAULT_DURATION)
            repeat = quiz.get("repeat", "One-time")
            message += (
                f"Chat: {chat_id}\nQuiz: {quiz['title']}\n"
                f"Time: {start_time}\nDuration: {duration}s\nRepeat: {repeat}\n\n"
            )
    await update.message.reply_text(message)

async def cancel_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("❌ You are not authorized to cancel schedules.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /cancel_schedule <chat_id> <quiz_title>")
        return
    try:
        chat_id = int(context.args[0])
        title = context.args[1]
        if chat_id not in SCHEDULED_QUIZZES:
            await update.message.reply_text("❌ No quizzes scheduled in this chat.")
            return
        quizzes = SCHEDULED_QUIZZES[chat_id]
        new_quizzes = [q for q in quizzes if q["title"] != title]
        if len(new_quizzes) == len(quizzes):
            await update.message.reply_text(
                f"❌ Quiz '{title}' not found in scheduled quizzes for this chat."
            )
            return
        SCHEDULED_QUIZZES[chat_id] = new_quizzes
        await update.message.reply_text(
            f"✅ Quiz '{title}' removed from scheduled quizzes for chat {chat_id}."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ---------------- AUTOMATIC QUIZ SCHEDULER ----------------
async def quiz_scheduler(app):
    while True:
        now = datetime.now()
        for chat_id, quizzes in list(SCHEDULED_QUIZZES.items()):
            for quiz in quizzes[:]:
                if quiz["start_time"] <= now:
                    data = load_quizzes()
                    questions = data[str(ADMINS[0])][quiz["title"]]
                    if RANDOMIZE_QUESTIONS:
                        random.shuffle(questions)
                    STATE[chat_id] = {"questions": questions, "current": 0, "poll_ids": [], "running": True}
                    SCORES[chat_id] = {}
                    await send_next_question(chat_id, app, open_period=quiz.get("timer", DEFAULT_DURATION))
                    if quiz.get("repeat") == "daily":
                        quiz["start_time"] += timedelta(days=1)
                    elif quiz.get("repeat") == "weekly":
                        quiz["start_time"] += timedelta(weeks=1)
                    else:
                        quizzes.remove(quiz)
        await asyncio.sleep(10)

# ---------------- BOT INITIALIZATION ----------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("createquiz", createquiz))
    app.add_handler(CommandHandler("addq", createquiz))
    app.add_handler(CommandHandler("myquizzes", myquizzes))
    app.add_handler(CommandHandler("deletequiz", deletequiz))
    app.add_handler(CommandHandler("hostquiz", hostquiz))
    app.add_handler(CommandHandler("stopquiz", stopquiz))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("schedule", schedule_quiz))
    app.add_handler(CommandHandler("view_schedules", view_schedules))
    app.add_handler(CommandHandler("cancel_schedule", cancel_schedule))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_txt))
    app.add_handler(MessageHandler(filters.Document.TEXT, handle_file))
    app.add_handler(MessageHandler(filters.Regex(r"^\d+$"), handle_timer_input))
    app.add_handler(PollAnswerHandler(poll_handler))
    print("🤖 QuizMaster Bot is running...")
    asyncio.create_task(quiz_scheduler(app))
    app.run_polling() 
