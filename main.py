import json, os, re, logging
from datetime import datetime
from typing import Dict, Any
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, PollAnswerHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# -----------------------------
# CONFIG (⚠️ hardcoded token for your request)
# -----------------------------
TOKEN = "7227173658:AAHB7Zj2EUXE5IHSCBBzOi8k2Ql76bLIkaA"

QUIZ_FILE = "quizzes.json"
SCHEDULE_FILE = "schedules.json"
USERS_FILE = "users.json"
SCORES_FILE = "scores.json"

# Runtime state (in-memory)
STATE: Dict[int, Dict[str, Any]] = {}     # chat_id -> {questions, current, message_ids, running}
SCORES: Dict[int, Dict[str, int]] = {}    # chat_id -> {user_id: score}
POLL_MAP: Dict[str, Dict[str, Any]] = {}  # poll_id -> {"chat_id": int, "correct": int}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s"
)
log = logging.getLogger("quizbot")

# -----------------------------
# UTILITIES
# -----------------------------
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def ensure_files():
    for f in (QUIZ_FILE, SCHEDULE_FILE, USERS_FILE, SCORES_FILE):
        if not os.path.exists(f):
            save_json(f, {})

def parse_questions(text):
    """
    Expected format per question block (blank line separates blocks):
    What is ...?
    A. option 1
    B. option 2 ✅
    C. option 3
    D. option 4
    Explanation: optional text
    """
    questions = []
    blocks = re.split(r'\n\s*\n', text.strip())
    for block in blocks:
        lines = [ln for ln in block.strip().split('\n') if ln.strip()]
        if len(lines) < 3:
            continue
        q = lines[0].strip()
        opts, ans, exp = [], -1, ""
        for i, line in enumerate(lines[1:]):
            if line.strip().lower().startswith("explanation:"):
                exp = line.split(":", 1)[-1].strip()
                continue
            mark_idx = i
            if '✅' in line or '✔️' in line:
                ans = mark_idx
                line = line.replace('✅', '').replace('✔️', '')
            line = re.sub(r"^[A-Da-d]\.\s*", "", line).strip()
            if line:
                opts.append(line)
        if ans != -1 and 2 <= len(opts) <= 10:
            questions.append((q, opts, ans, exp))
    return questions

def leaderboard_text(scores_map: Dict[str, int]) -> str:
    if not scores_map:
        return "No participants yet."
    sorted_scores = sorted(scores_map.items(), key=lambda x: x[1], reverse=True)
    lines = ["🏆 Leaderboard"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, score) in enumerate(sorted_scores, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        lines.append(f"{medal} User {uid} — {score} pts")
    return "\n".join(lines)

# -----------------------------
# COMMANDS
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Create Quiz", callback_data="ui:create")],
        [InlineKeyboardButton("My Quizzes", callback_data="ui:my")],
        [InlineKeyboardButton("Join Quiz", callback_data="ui:join")],
        [InlineKeyboardButton("Leaderboard", callback_data="ui:leader")]
    ]
    await update.message.reply_text(
        "👋 Welcome to QuizMaster Pro!\n"
        "Use the buttons below or commands:\n"
        "/createquiz <title> — create a quiz\n"
        "/hostquiz <title> — start in this chat\n"
        "/schedulequiz <title> <HH:MM> <chat_id> [daily|weekly|once]\n"
        "/myquizzes — list your quizzes\n"
        "/leaderboard [global] — show ranks",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def create_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /createquiz <quiz title>")
        return
    title = " ".join(context.args).strip()
    quizzes = load_json(QUIZ_FILE)
    quizzes.setdefault(user, {})
    if title in quizzes[user]:
        await update.message.reply_text("A quiz with that title already exists.")
        return
    quizzes[user][title] = {"questions": [], "category": "", "description": ""}
    save_json(QUIZ_FILE, quizzes)
    context.user_data["current_quiz"] = title
    await update.message.reply_text(
        f"✅ Quiz '{title}' created.\n\n"
        "Now send questions in text blocks or upload a .txt/.json file.\n"
        "Example block:\n\n"
        "What is 2+2?\n"
        "A. 3\nB. 4 ✅\nC. 5\nD. 22\n"
        "Explanation: Basic math."
    )

async def my_quizzes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    quizzes = load_json(QUIZ_FILE)
    if user not in quizzes or not quizzes[user]:
        await update.message.reply_text("You have no quizzes yet.")
        return
    titles = "\n".join(f"• {t}" for t in quizzes[user].keys())
    await update.message.reply_text(f"📚 Your quizzes:\n{titles}")

async def delete_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /deletequiz <quiz title>")
        return
    title = " ".join(context.args).strip()
    quizzes = load_json(QUIZ_FILE)
    if user in quizzes and title in quizzes[user]:
        del quizzes[user][title]
        save_json(QUIZ_FILE, quizzes)
        await update.message.reply_text(f"🗑️ Deleted quiz '{title}'.")
    else:
        await update.message.reply_text("Quiz not found.")

async def host_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually start a quiz in the current chat: /hostquiz <title>"""
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /hostquiz <quiz title>")
        return
    title = " ".join(context.args).strip()
    await start_quiz_in_chat(chat_id, title, context)

async def join_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    users = load_json(USERS_FILE)
    users.setdefault(user, {"quizzes_played": 0, "total_points": 0, "reminders": True})
    save_json(USERS_FILE, users)
    await update.message.reply_text(f"✅ Registered for quizzes. Welcome, User {user}!")

async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = [a.lower() for a in context.args] if context.args else []
    if args and args[0] == "global":
        global_scores = load_json(SCORES_FILE)
        text = leaderboard_text(global_scores)
        await update.message.reply_text("🌍 Global " + text)
        return
    chat_id = update.effective_chat.id
    text = leaderboard_text(SCORES.get(chat_id, {}))
    await update.message.reply_text(text)

async def schedule_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /schedulequiz <title> <HH:MM> <chat_id> [daily|weekly|once]
    Times use server time zone. Example:
    /schedulequiz "Sample Quiz" 21:00 -1001234567890 daily
    """
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /schedulequiz <title> <HH:MM> <chat_id> [daily|weekly|once]")
        return
    title = context.args[0]
    time_str = context.args[1]
    chat_id_str = context.args[2]
    repeat = context.args[3].lower() if len(context.args) > 3 else "once"
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await update.message.reply_text("Time must be HH:MM (24h).")
        return
    schedules = load_json(SCHEDULE_FILE)
    schedules.setdefault(chat_id_str, [])
    schedules[chat_id_str].append({"title": title, "time": time_str, "repeat": repeat})
    save_json(SCHEDULE_FILE, schedules)
    await update.message.reply_text(f"⏰ Scheduled '{title}' at {time_str} for chat {chat_id_str} ({repeat}).")

# -----------------------------
# MESSAGE / FILE HANDLERS
# -----------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if "current_quiz" not in context.user_data:
        return  # ignore free text
    title = context.user_data["current_quiz"]
    qs = parse_questions(update.message.text)
    if not qs:
        await update.message.reply_text("No valid questions found in your text.")
        return
    quizzes = load_json(QUIZ_FILE)
    quizzes[user][title]["questions"].extend(qs)
    save_json(QUIZ_FILE, quizzes)
    await update.message.reply_text(f"✅ Added {len(qs)} question(s) to '{title}'.")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = str(update.effective_user.id)
    if "current_quiz" not in context.user_data:
        await update.message.reply_text("Use /createquiz <title> first, then upload the file.")
        return
    doc = update.message.document
    if not doc.file_name.endswith((".txt", ".json")):
        await update.message.reply_text("Only .txt or .json files supported.")
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
        qs = [(item["q"], item["opts"], int(item["ans"]), item.get("exp", "")) for item in raw]
    quizzes = load_json(QUIZ_FILE)
    title = context.user_data["current_quiz"]
    quizzes[user][title]["questions"].extend(qs)
    save_json(QUIZ_FILE, quizzes)
    await update.message.reply_text(f"✅ Imported {len(qs)} question(s) to '{title}' from {doc.file_name}.")

# -----------------------------
# QUIZ FLOW
# -----------------------------
async def start_quiz_in_chat(chat_id: int, quiz_title: str, context: ContextTypes.DEFAULT_TYPE):
    quizzes = load_json(QUIZ_FILE)
    found = None
    for owner_id, qsmap in quizzes.items():
        if quiz_title in qsmap and qsmap[quiz_title]["questions"]:
            found = qsmap[quiz_title]["questions"]
            break
    if not found:
        await context.bot.send_message(chat_id, f"❌ Quiz '{quiz_title}' not found or has no questions.")
        return
    STATE[chat_id] = {"questions": found, "current": 0, "message_ids": [], "running": True}
    SCORES[chat_id] = {}
    await send_next_question(chat_id, context)

async def send_next_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    state = STATE.get(chat_id)
    if not state or not state.get("running"):
        return
    if state["current"] >= len(state["questions"]):
        # Quiz finished
        text = leaderboard_text(SCORES.get(chat_id, {}))
        await context.bot.send_message(chat_id, f"✅ Quiz Finished!\n\n{text}")
        STATE.pop(chat_id, None)
        SCORES.pop(chat_id, None)
        return

    q, opts, ans_idx, explanation = state["questions"][state["current"]]
    msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Q{state['current']+1}/{len(state['questions'])}: {q}",
        options=opts,
        type=Poll.QUIZ,
        correct_option_id=ans_idx,
        explanation=explanation[:200],   # Telegram limit
        is_anonymous=False,
        open_period=25                   # seconds to answer
    )
    # Map poll -> correct answer and chat
    if msg.poll:
        POLL_MAP[msg.poll.id] = {"chat_id": chat_id, "correct": ans_idx}

    state["message_ids"].append(msg.message_id)
    state["current"] += 1

    # Admin controls
    keyboard = [
        [InlineKeyboardButton("Next ▶️", callback_data=f"ctrl:next:{chat_id}")],
        [InlineKeyboardButton("Stop ⏹️", callback_data=f"ctrl:stop:{chat_id}")],
        [InlineKeyboardButton("Leaderboard 🏆", callback_data=f"ctrl:leader:{chat_id}")]
    ]
    await context.bot.send_message(chat_id, "Admin Controls:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) < 2:
        return
    action = parts[1]
    # Prefer chat id from message chat
    chat_id = query.message.chat_id if query.message else None
    if len(parts) >= 3:
        try:
            chat_id = int(parts[2])
        except:
            pass
    if chat_id is None:
        return
    if action == "next":
        await send_next_question(chat_id, context)
    elif action == "stop":
        if chat_id in STATE:
            STATE[chat_id]["running"] = False
            await query.edit_message_text("🛑 Quiz stopped.")
    elif action == "leader":
        await query.edit_message_text(leaderboard_text(SCORES.get(chat_id, {})))

async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Scores a user when they answer a poll correctly.
    """
    pa = update.poll_answer
    poll_id = pa.poll_id
    user_id = str(pa.user.id)
    mapping = POLL_MAP.get(poll_id)
    if not mapping:
        return
    chat_id = mapping["chat_id"]
    correct = mapping["correct"]
    selected = pa.option_ids[0] if pa.option_ids else None
    if selected is None:
        return
    # Initialize score map for chat
    SCORES.setdefault(chat_id, {})
    # Correct answer?
    if selected == correct:
        SCORES[chat_id][user_id] = SCORES[chat_id].get(user_id, 0) + 1
        # Update global totals
        global_scores = load_json(SCORES_FILE)
        global_scores[user_id] = global_scores.get(user_id, 0) + 1
        save_json(SCORES_FILE, global_scores)
        # Update user totals
        users = load_json(USERS_FILE)
        users.setdefault(user_id, {"quizzes_played": 0, "total_points": 0, "reminders": True})
        users[user_id]["total_points"] += 1
        save_json(USERS_FILE, users)

# -----------------------------
# AUTOMATION / SCHEDULER
# -----------------------------
async def scheduler_tick(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every minute; if any schedule matches current HH:MM, triggers quiz.
    """
    now = datetime.now().strftime("%H:%M")
    schedules = load_json(SCHEDULE_FILE)
    for chat_id_str, items in schedules.items():
        for item in items:
            if item.get("time") == now:
                # optional repeat types: once/daily/weekly — we treat all the same here
                try:
                    chat_id = int(chat_id_str)
                except:
                    continue
                await start_quiz_in_chat(chat_id, item.get("title", ""), context)
                # If once, remove it
                if item.get("repeat", "once") == "once":
                    items.remove(item)
                    save_json(SCHEDULE_FILE, schedules)

# -----------------------------
# MAIN
# -----------------------------
def main():
    ensure_files()
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("createquiz", create_quiz_cmd))
    app.add_handler(CommandHandler("myquizzes", my_quizzes_cmd))
    app.add_handler(CommandHandler("deletequiz", delete_quiz_cmd))
    app.add_handler(CommandHandler("hostquiz", host_quiz_cmd))
    app.add_handler(CommandHandler("joinquiz", join_quiz_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("schedulequiz", schedule_quiz_cmd))

    # Text & files for adding questions
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(button_handler, pattern=r"^ui:|^ctrl:"))

    # Poll answers
    app.add_handler(PollAnswerHandler(poll_answer_handler))

    # Scheduler (every minute)
    app.job_queue.run_repeating(scheduler_tick, interval=60, first=10)

    log.info("🤖 QuizMaster Pro Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
