import json, os, re, logging, asyncio
from datetime import datetime
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, PollAnswerHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# -----------------------------
# CONFIG
# -----------------------------
TOKEN = os.getenv("7227173658:AAHB7Zj2EUXE5IHSCBBzOi8k2Ql76bLIkaA")
QUIZ_FILE = "quizzes.json"
SCHEDULE_FILE = "schedules.json"
USERS_FILE = "users.json"
SCORES_FILE = "scores.json"

STATE = {}   # chat_id -> current quiz state
SCORES = {}  # chat_id -> {user_id: score}

logging.basicConfig(level=logging.INFO)

# -----------------------------
# UTILITY FUNCTIONS
# -----------------------------
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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

def generate_leaderboard(chat_id, global_rank=False):
    scores = SCORES.get(chat_id, {}) if not global_rank else load_json(SCORES_FILE)
    if not scores:
        return "No participants yet."
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    text = "🏆 Leaderboard:\n"
    for i, (uid, score) in enumerate(sorted_scores, 1):
        text += f"{i}. User {uid} - {score} pts\n"
    return text

# -----------------------------
# USER COMMANDS
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Create Quiz", callback_data="create_quiz")],
        [InlineKeyboardButton("My Quizzes", callback_data="my_quizzes")],
        [InlineKeyboardButton("Join Quiz", callback_data="join_quiz")],
        [InlineKeyboardButton("Leaderboard", callback_data="leaderboard")]
    ]
    await update.message.reply_text(
        "👋 Welcome to QuizMaster Pro!\n"
        "Use the buttons below to start.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /createquiz <quiz title>")
        return
    title = " ".join(context.args)
    quizzes = load_json(QUIZ_FILE)
    quizzes.setdefault(user, {})[title] = {"questions": [], "category": "", "description": ""}
    save_json(QUIZ_FILE, quizzes)
    context.user_data["current_quiz"] = title
    await update.message.reply_text(f"✅ Quiz '{title}' created. Send questions or upload a .txt/.json file.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if "current_quiz" not in context.user_data:
        await update.message.reply_text("Use /createquiz first.")
        return
    title = context.user_data["current_quiz"]
    qs = parse_questions(update.message.text)
    if not qs:
        await update.message.reply_text("No valid questions found.")
        return
    quizzes = load_json(QUIZ_FILE)
    quizzes[user][title]["questions"].extend(qs)
    save_json(QUIZ_FILE, quizzes)
    await update.message.reply_text(f"✅ {len(qs)} question(s) added to '{title}'.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if "current_quiz" not in context.user_data:
        await update.message.reply_text("Use /createquiz first.")
        return
    doc = update.message.document
    if not doc.file_name.endswith((".txt", ".json")):
        await update.message.reply_text("Only .txt or .json supported.")
        return
    file = await doc.get_file()
    path = f"{doc.file_id}_{doc.file_name}"
    await file.download_to_drive(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    os.remove(path)
    if doc.file_name.endswith(".txt"):
        qs = parse_questions(content)
    else:
        raw = json.loads(content)
        qs = [(item["q"], item["opts"], item["ans"], item.get("exp", "")) for item in raw]
    quizzes = load_json(QUIZ_FILE)
    title = context.user_data["current_quiz"]
    quizzes[user][title]["questions"].extend(qs)
    save_json(QUIZ_FILE, quizzes)
    await update.message.reply_text(f"✅ {len(qs)} question(s) added to '{title}'.")

async def join_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    users = load_json(USERS_FILE)
    users[user] = users.get(user, {"quizzes_played": 0, "total_points": 0})
    save_json(USERS_FILE, users)
    await update.message.reply_text(f"✅ You are registered for quizzes, User {user}!")

# -----------------------------
# QUIZ FLOW
# -----------------------------
async def send_next_question(chat_id, context: ContextTypes.DEFAULT_TYPE):
    state = STATE.get(chat_id)
    if not state or not state["running"]:
        return
    if state["current"] >= len(state["questions"]):
        leaderboard_text = generate_leaderboard(chat_id)
        await context.bot.send_message(chat_id, f"✅ Quiz Finished!\n\n{leaderboard_text}")
        STATE.pop(chat_id)
        SCORES.pop(chat_id)
        return
    q, opts, ans_idx, explanation = state["questions"][state["current"]]
    poll = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Q{state['current']+1}/{len(state['questions'])}: {q}",
        options=opts,
        type=Poll.QUIZ,
        correct_option_id=ans_idx,
        explanation=explanation,
        is_anonymous=False,
        open_period=30
    )
    state["message_ids"].append(poll.message_id)
    state["current"] += 1
    keyboard = [
        [InlineKeyboardButton("Next ▶️", callback_data=f"next_{chat_id}")],
        [InlineKeyboardButton("Stop ⏹️", callback_data=f"stop_{chat_id}")],
        [InlineKeyboardButton("Leaderboard 🏆", callback_data=f"leader_{chat_id}")]
    ]
    await context.bot.send_message(chat_id, "Admin Controls:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    if data.startswith("next_"):
        await send_next_question(chat_id, context)
    elif data.startswith("stop_"):
        if chat_id in STATE:
            STATE[chat_id]["running"] = False
            await query.edit_message_text("🛑 Quiz stopped by admin.")
    elif data.startswith("leader_"):
        text = generate_leaderboard(chat_id)
        await query.edit_message_text(text)

async def poll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.poll_answer.user.id)
    for cid in STATE:
        if STATE[cid]["running"]:
            SCORES[cid][user_id] = SCORES[cid].get(user_id, 0) + 1
            # Update global scores
            global_scores = load_json(SCORES_FILE)
            global_scores[user_id] = global_scores.get(user_id, 0) + 1
            save_json(SCORES_FILE, global_scores)

# -----------------------------
# AUTOMATION
# -----------------------------
async def send_quiz_to_chat(chat_id, quiz_title, context):
    quizzes = load_json(QUIZ_FILE)
    for user in quizzes:
        if quiz_title in quizzes[user]:
            STATE[chat_id] = {
                "questions": quizzes[user][quiz_title]["questions"],
                "current": 0,
                "message_ids": [],
                "running": True
            }
            SCORES[
