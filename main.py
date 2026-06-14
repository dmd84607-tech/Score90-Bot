import os
import json
import random
from datetime import datetime, date
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from groq import Groq

# ─── JSON SE QUESTIONS LOAD KARO ─────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_questions(filename):
    path = os.path.join(BASE_DIR, filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ {filename}: {len(data)} questions load ho gaye!")
        return data
    except FileNotFoundError:
        print(f"❌ {filename} nahi mili!")
        return []

SCIENCE_PYQ       = load_questions('science_data.json')
SOCIAL_SCIENCE_PYQ = load_questions('social_data.json')
HINDI_PYQ         = load_questions('hindi_data.json')
MAITHILI_PYQ      = load_questions('maithili_data.json')
NON_HINDI_PYQ     = load_questions('nonhindi_data.json')

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
ADMIN_ID       = str(os.environ.get("ADMIN_ID", "0").strip())

bot    = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

BOT_NAME = "Score90"

SUBJECT_BANKS = {
    "quiz_science"   : ("🔬 Science",        SCIENCE_PYQ),
    "quiz_social"    : ("🌍 Social Science", SOCIAL_SCIENCE_PYQ),
    "quiz_hindi"     : ("📖 Hindi",          HINDI_PYQ),
    "quiz_maithili"  : ("🌸 Maithili",       MAITHILI_PYQ),
    "quiz_nonhindi"  : ("📗 Non-Hindi",      NON_HINDI_PYQ),
}

# ─── DATA STORES ─────────────────────────────────────────────
all_users      = set()
user_lang      = {}   # uid -> "hi" | "hl"
user_scores    = {}   # uid -> int  (total correct)
question_count = {}   # uid -> int  (total attempted)
subject_stats  = {}   # uid -> {subj: [correct, total]}
wrong_answers  = {}   # uid -> [quiz_dict, ...]
active_quiz    = {}   # uid -> quiz dict (with subject_cb, hint_used)
streaks        = {}   # uid -> {"last_date": date, "count": int}

# ─── HELPERS ─────────────────────────────────────────────────

def is_admin(uid):   return str(uid) == ADMIN_ID
def lang(uid):       return user_lang.get(uid, "hl")
def other_lang(uid): return "hi" if lang(uid) == "hl" else "hl"

def lang_label(uid):
    return "हिंदी (Devanagari)" if lang(uid) == "hi" else "Hinglish (Roman)"

def toggle_label(uid):
    return "🔡 Hindi Font mein badlo" if lang(uid) == "hl" else "🔤 Hinglish Font mein badlo"

def get_question(q, uid):
    # New format: 'question' key | Old format: 'hi'/'hl'
    if "question" in q:
        return q["question"]
    return q["hi"] if lang(uid) == "hi" else q["hl"]

def get_options(q, uid):
    # New format: 'options' key | Old format: 'options_hi'/'options_hl'
    if "options" in q:
        return q["options"]
    return q["options_hi"] if lang(uid) == "hi" else q["options_hl"]

def get_exp(q, uid):
    # New format: 'hint' key | Old format: 'exp_hi'/'exp_hl'
    if "hint" in q:
        return q["hint"]
    return q["exp_hi"] if lang(uid) == "hi" else q["exp_hl"]

def get_correct(q):
    # New format: 'correct_index' | Old format: 'correct'
    return q.get("correct_index", q.get("correct", 0))

def get_year(q):
    # New format: year embedded in question | Old format: 'year' key
    return q.get("year", "")

def get_qid(q):
    # Unique ID for deduplication (avoid repeating same question)
    return q.get("question", q.get("hi", ""))

def track(uid, subj_key=None, correct=None):
    all_users.add(uid)
    question_count[uid] = question_count.get(uid, 0) + 1
    if subj_key and correct is not None:
        if uid not in subject_stats:
            subject_stats[uid] = {}
        if subj_key not in subject_stats[uid]:
            subject_stats[uid][subj_key] = [0, 0]
        subject_stats[uid][subj_key][1] += 1
        if correct:
            subject_stats[uid][subj_key][0] += 1

def update_streak(uid):
    today = date.today()
    s = streaks.get(uid, {"last_date": None, "count": 0})
    if s["last_date"] == today:
        return s["count"]
    if s["last_date"] and (today - s["last_date"]).days == 1:
        s["count"] += 1
    else:
        s["count"] = 1
    s["last_date"] = today
    streaks[uid] = s
    return s["count"]

def get_streak(uid):
    s = streaks.get(uid, {"last_date": None, "count": 0})
    if s["last_date"] and (date.today() - s["last_date"]).days > 1:
        return 0
    return s["count"]

def ai_hint(question_text, options):
    opts = "\n".join([f"{['A','B','C','D'][i]}. {o}" for i, o in enumerate(options)])
    prompt = (
        f"Bihar Board Class 10 ke is PYQ sawaal ka sirf ek chhota hint do (answer mat batao, "
        f"sirf sochne mein madad karo, 1-2 lines mein):\n\nSawaal: {question_text}\n{opts}"
    )
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.5
        )
        return resp.choices[0].message.content.strip()
    except:
        return "Sawaal ko dhyan se padho aur options compare karo! 🤔"

def ai_explain_wrong(quiz, uid):
    q   = get_question(quiz, uid)
    opts = get_options(quiz, uid)
    correct_opt = opts[get_correct(quiz)]
    prompt = (
        f"Bihar Board Class 10 ka yeh PYQ sawaal student ne galat kiya. "
        f"Simple Hinglish mein detail mein samjhao kyun '{correct_opt}' sahi hai aur concept bhi batao:\n\n"
        f"Sawaal: {q}\nSahi Jawab: {correct_opt}"
    )
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.6
        )
        return resp.choices[0].message.content.strip()
    except:
        return get_exp(quiz, uid)

# ─── KEYBOARDS ───────────────────────────────────────────────

def main_menu(uid=None):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔬 Science",        callback_data="quiz_science"),
        InlineKeyboardButton("🌍 Social Science", callback_data="quiz_social"),
    )
    markup.row(
        InlineKeyboardButton("📖 Hindi",          callback_data="quiz_hindi"),
        InlineKeyboardButton("🌸 Maithili",       callback_data="quiz_maithili"),
    )
    markup.row(
        InlineKeyboardButton("📗 Non-Hindi",      callback_data="quiz_nonhindi"),
    )
    markup.row(
        InlineKeyboardButton("🏆 Score & Stats",  callback_data="my_score"),
        InlineKeyboardButton("🏅 Leaderboard",    callback_data="leaderboard"),
    )
    markup.row(
        InlineKeyboardButton("📝 Review Galat Jawab", callback_data="wrong_review"),
    )
    if uid:
        markup.row(InlineKeyboardButton(toggle_label(uid), callback_data="toggle_lang"))
    return markup

def quiz_markup(options, hint_used=False):
    markup = InlineKeyboardMarkup()
    letters = ['A', 'B', 'C', 'D']
    for i, opt in enumerate(options):
        markup.row(InlineKeyboardButton(f"{letters[i]}.  {opt}", callback_data=f"qa_{i}"))
    if not hint_used:
        markup.row(InlineKeyboardButton("💡 Hint lo", callback_data="get_hint"))
    markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    return markup

def after_quiz_markup(subject_cb):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("▶️ Agla Sawaal",   callback_data=subject_cb),
        InlineKeyboardButton("🏠 Menu",          callback_data="main_menu"),
    )
    markup.row(InlineKeyboardButton("🧠 Aur Detail mein Samjhao", callback_data="explain_more"))
    return markup

def review_markup(idx, total):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💡 Hint",          callback_data=f"review_hint_{idx}"),
        InlineKeyboardButton("🧠 Detail Samjhao", callback_data=f"review_explain_{idx}"),
    )
    if idx + 1 < total:
        markup.row(InlineKeyboardButton(f"▶️ Agla ({idx+2}/{total})", callback_data=f"review_next_{idx+1}"))
    markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    return markup

# ─── WELCOME ─────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid   = message.from_user.id
    name  = message.from_user.first_name or "Bhai"
    all_users.add(uid)
    streak = get_streak(uid)
    score  = user_scores.get(uid, 0)
    total  = question_count.get(uid, 0)
    acc    = round(score / total * 100) if total else 0
    fire   = "🔥" * min(streak, 5) if streak else "—"
    bot.send_message(
        message.chat.id,
        f"👑 *Swagat hai Topper, {name}!*\n"
        f"*{BOT_NAME}* mein aapka aagman ho chuka hai 🎉\n"
        f"Banaya hai *Dilshad Iqbal Faruqui* ne 🙏\n\n"
        f"🎯 Mission: *Bihar Board mein 400+ Marks!*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🏆 Score: *{score}*  |  📊 Accuracy: *{acc}%*\n"
        f"🔥 Streak: *{streak} din* {fire}\n"
        f"🌐 Bhasha: *{lang_label(uid)}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Chalo shuru karte hain! 💪\n"
        f"Aaj kaun se subject mein garda udana hai? 👇",
        reply_markup=main_menu(uid),
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['language'])
def cmd_language(message):
    uid = message.from_user.id
    user_lang[uid] = other_lang(uid)
    bot.reply_to(message,
        f"✅ Bhasha badal gayi! Ab *{lang_label(uid)}* mein padh rahe ho.",
        reply_markup=main_menu(uid), parse_mode='Markdown')

@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    uid = message.from_user.id
    if not is_admin(uid):
        bot.reply_to(message, "❌ Sirf admin!"); return
    total_q = sum(question_count.values())
    top = sorted(user_scores.items(), key=lambda x: x[1], reverse=True)[:5]
    top_text = "\n".join([f"  {i+1}. User {u}: {s} pts" for i,(u,s) in enumerate(top)]) or "  Koi nahi abhi"
    bot.reply_to(message,
        f"👑 *Admin Stats — {BOT_NAME}*\n\n"
        f"👥 Users: *{len(all_users)}*\n"
        f"❓ Total Sawaal: *{total_q}*\n\n"
        f"🏆 Top Users:\n{top_text}",
        parse_mode='Markdown')

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Sirf admin!"); return
    text = message.text.replace('/broadcast', '', 1).strip()
    if not text:
        bot.reply_to(message, "Usage: /broadcast <message>"); return
    ok, fail = 0, 0
    for u in all_users:
        try:
            bot.send_message(u, f"📢 *{BOT_NAME} Update:*\n\n{text}", parse_mode='Markdown')
            ok += 1
        except:
            fail += 1
    bot.reply_to(message, f"✅ {ok} users ko bheja, {fail} fail.")

# ─── CALLBACKS ────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "main_menu")
def cb_menu(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    streak = get_streak(uid)
    fire = "🔥" * min(streak, 5) if streak else "—"
    bot.send_message(call.message.chat.id,
        f"📚 *{BOT_NAME}* — Subject chuno\n"
        f"🔥 Streak: *{streak} din* {fire} | 🌐 *{lang_label(uid)}*",
        reply_markup=main_menu(uid), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data == "toggle_lang")
def cb_toggle(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    user_lang[uid] = other_lang(uid)
    bot.send_message(call.message.chat.id,
        f"✅ *Bhasha badal gayi!*\nAb *{lang_label(uid)}* mein padh rahe ho. 📖",
        reply_markup=main_menu(uid), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data == "my_score")
def cb_score(call):
    bot.answer_callback_query(call.id)
    uid   = call.from_user.id
    score = user_scores.get(uid, 0)
    total = question_count.get(uid, 0)
    acc   = round(score / total * 100) if total else 0
    streak = get_streak(uid)

    subj_text = ""
    if uid in subject_stats:
        lines = []
        icons = {"quiz_science":"🔬","quiz_social":"🌍","quiz_hindi":"📖","quiz_maithili":"🌸"}
        for k, (c2, t2) in subject_stats[uid].items():
            a2 = round(c2/t2*100) if t2 else 0
            bar = "█" * (a2 // 20) + "░" * (5 - a2 // 20)
            lines.append(f"{icons.get(k,'📚')} {bar} {a2}% ({c2}/{t2})")
        subj_text = "\n\n📊 *Subject-wise Report:*\n" + "\n".join(lines)

    wrong_cnt = len(wrong_answers.get(uid, []))
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    bot.send_message(call.message.chat.id,
        f"🏆 *Tumhara Score — {BOT_NAME}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"✅ Sahi: *{score}*  ❌ Galat: *{total - score}*\n"
        f"❓ Total: *{total}*  📊 Accuracy: *{acc}%*\n"
        f"🔥 Streak: *{streak} din*\n"
        f"📝 Review ke liye: *{wrong_cnt} sawaal*"
        f"{subj_text}",
        reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data == "leaderboard")
def cb_leader(call):
    bot.answer_callback_query(call.id)
    top = sorted(user_scores.items(), key=lambda x: x[1], reverse=True)[:10]
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    uid = call.from_user.id
    lines = []
    my_rank = None
    for i, (u, s) in enumerate(top):
        acc = round(user_scores.get(u,0) / question_count.get(u,1) * 100)
        tag = " ← *Tum*" if u == uid else ""
        lines.append(f"{medals[i]} Score *{s}* | Acc *{acc}%*{tag}")
        if u == uid:
            my_rank = i + 1

    if not lines:
        lines = ["Abhi koi data nahi — quiz khelo!"]

    my_score = user_scores.get(uid, 0)
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    bot.send_message(call.message.chat.id,
        f"🏅 *Leaderboard — {BOT_NAME}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines) +
        f"\n━━━━━━━━━━━━━━━━━\n"
        f"Tumhara Rank: *{'#' + str(my_rank) if my_rank else 'Top 10 mein nahi abhi'}*\n"
        f"Tumhara Score: *{my_score}*",
        reply_markup=markup, parse_mode='Markdown')

# ─── QUIZ FLOW ────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data in SUBJECT_BANKS)
def cb_subject(call):
    bot.answer_callback_query(call.id)
    uid        = call.from_user.id
    subj_key   = call.data
    label, bank = SUBJECT_BANKS[subj_key]

    last_q = active_quiz.get(uid, {}).get("_qid", "")
    pool   = [q for q in bank if get_qid(q) != last_q] or bank
    quiz   = random.choice(pool)

    active_quiz[uid] = {**quiz, "subject_cb": subj_key, "_qid": get_qid(quiz), "hint_used": False}

    streak  = update_streak(uid)
    score   = user_scores.get(uid, 0)
    total   = question_count.get(uid, 0) + 1
    question= get_question(quiz, uid)
    options = get_options(quiz, uid)
    year    = get_year(quiz)
    fire    = f" 🔥×{streak}" if streak > 1 else ""
    year_tag = f" — PYQ {year}" if year else ""

    bot.send_message(
        call.message.chat.id,
        f"📚 *{label}*{year_tag}{fire}\n"
        f"🏆 Score: *{score}* | ❓ #{total}\n\n"
        f"❓ {question}",
        reply_markup=quiz_markup(options, hint_used=False),
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == "get_hint")
def cb_hint(call):
    bot.answer_callback_query(call.id, "💡 Hint la raha hoon...")
    uid  = call.from_user.id
    quiz = active_quiz.get(uid)
    if not quiz:
        bot.send_message(call.message.chat.id, "Quiz expire! Dobara shuru karo.", reply_markup=main_menu(uid))
        return
    thinking = bot.send_message(call.message.chat.id, "💡 Hint soch raha hoon... 🤔")
    q_text  = get_question(quiz, uid)
    options = get_options(quiz, uid)
    hint    = ai_hint(q_text, options)
    bot.delete_message(call.message.chat.id, thinking.message_id)
    active_quiz[uid]["hint_used"] = True
    bot.send_message(call.message.chat.id,
        f"💡 *Hint:*\n_{hint}_",
        reply_markup=quiz_markup(options, hint_used=True),
        parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data.startswith("qa_"))
def cb_answer(call):
    bot.answer_callback_query(call.id)
    uid    = call.from_user.id
    quiz   = active_quiz.get(uid)
    if not quiz:
        bot.send_message(call.message.chat.id, "⚠️ Quiz expire! Naya subject chuno.", reply_markup=main_menu(uid))
        return

    chosen     = int(call.data.split("_")[1])
    correct    = get_correct(quiz)
    options    = get_options(quiz, uid)
    exp        = get_exp(quiz, uid)
    letters    = ['A','B','C','D']
    is_correct = (chosen == correct)

    question_count[uid] = question_count.get(uid, 0) + 1
    track(uid, quiz["subject_cb"], is_correct)

    if is_correct:
        user_scores[uid] = user_scores.get(uid, 0) + 1
        header = "✅ *Bilkul Sahi!* +1 point 🎉"
    else:
        header = f"❌ *Galat!* Sahi tha: *{letters[correct]}. {options[correct]}*"
        wr = wrong_answers.setdefault(uid, [])
        if not any(get_qid(w) == get_qid(quiz) for w in wr):
            wr.append(quiz)

    last_quiz_store = active_quiz.get(uid, {})
    last_quiz_store["_last_quiz"] = quiz
    active_quiz[uid] = last_quiz_store

    score  = user_scores.get(uid, 0)
    total  = question_count.get(uid, 0)
    acc    = round(score / total * 100) if total else 0
    streak = get_streak(uid)
    year   = get_year(quiz)
    year_line = f"📅 Year: *{year}* | " if year else ""

    bot.send_message(
        call.message.chat.id,
        f"{header}\n\n"
        f"💡 *Explanation:*\n{exp}\n\n"
        f"{year_line}🏆 Score: *{score}* | 📊 Acc: *{acc}%*\n"
        f"🔥 Streak: *{streak} din*",
        reply_markup=after_quiz_markup(quiz['subject_cb']),
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda c: c.data == "explain_more")
def cb_explain_more(call):
    bot.answer_callback_query(call.id, "🧠 Detail samjha raha hoon...")
    uid  = call.from_user.id
    quiz = active_quiz.get(uid, {}).get("_last_quiz")
    if not quiz:
        bot.send_message(call.message.chat.id, "⚠️ Sawaal nahi mila!", reply_markup=main_menu(uid))
        return
    thinking = bot.send_message(call.message.chat.id, "🧠 AI detail mein samjha raha hai... ⏳")
    detail   = ai_explain_wrong(quiz, uid)
    bot.delete_message(call.message.chat.id, thinking.message_id)
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("▶️ Agla Sawaal", callback_data=quiz["subject_cb"]),
        InlineKeyboardButton("🏠 Menu",        callback_data="main_menu"),
    )
    bot.send_message(call.message.chat.id,
        f"🧠 *Deep Explanation:*\n\n{detail}",
        reply_markup=markup, parse_mode='Markdown')

# ─── WRONG ANSWER REVIEW ─────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "wrong_review")
def cb_wrong_review(call):
    bot.answer_callback_query(call.id)
    uid   = call.from_user.id
    wrongs = wrong_answers.get(uid, [])
    if not wrongs:
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
        bot.send_message(call.message.chat.id,
            "🎉 *Mast hai!* Abhi tak koi galat jawab nahi!\nQuiz khelte raho! 💪",
            reply_markup=markup, parse_mode='Markdown')
        return
    _send_review_question(call.message.chat.id, uid, 0)

def _send_review_question(chat_id, uid, idx):
    wrongs  = wrong_answers.get(uid, [])
    if idx >= len(wrongs):
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
        bot.send_message(chat_id, "✅ Sab galat sawaal review ho gaye!", reply_markup=markup)
        return
    quiz    = wrongs[idx]
    q_text  = get_question(quiz, uid)
    options = get_options(quiz, uid)
    letters = ['A','B','C','D']
    correct = get_correct(quiz)
    sahi    = f"{letters[correct]}. {options[correct]}"
    year    = get_year(quiz)
    year_tag = f" — PYQ {year}" if year else ""
    bot.send_message(chat_id,
        f"📝 *Review ({idx+1}/{len(wrongs)}){year_tag}*\n\n"
        f"❓ {q_text}\n\n"
        f"✅ Sahi Jawab: *{sahi}*",
        reply_markup=review_markup(idx, len(wrongs)),
        parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data.startswith("review_next_"))
def cb_review_next(call):
    bot.answer_callback_query(call.id)
    idx = int(call.data.split("_")[-1])
    _send_review_question(call.message.chat.id, call.from_user.id, idx)

@bot.callback_query_handler(func=lambda c: c.data.startswith("review_hint_"))
def cb_review_hint(call):
    bot.answer_callback_query(call.id, "💡 Hint la raha hoon...")
    uid  = call.from_user.id
    idx  = int(call.data.split("_")[-1])
    quiz = wrong_answers.get(uid, [])[idx] if wrong_answers.get(uid) else None
    if not quiz:
        bot.send_message(call.message.chat.id, "⚠️ Sawaal nahi mila!"); return
    thinking = bot.send_message(call.message.chat.id, "💡 Hint soch raha hoon... 🤔")
    hint = ai_hint(get_question(quiz, uid), get_options(quiz, uid))
    bot.delete_message(call.message.chat.id, thinking.message_id)
    bot.send_message(call.message.chat.id, f"💡 *Hint:*\n_{hint}_", parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data.startswith("review_explain_"))
def cb_review_explain(call):
    bot.answer_callback_query(call.id, "🧠 Samjha raha hoon...")
    uid  = call.from_user.id
    idx  = int(call.data.split("_")[-1])
    quiz = wrong_answers.get(uid, [])[idx] if wrong_answers.get(uid) else None
    if not quiz:
        bot.send_message(call.message.chat.id, "⚠️ Sawaal nahi mila!"); return
    thinking = bot.send_message(call.message.chat.id, "🧠 AI samjha raha hai... ⏳")
    detail   = ai_explain_wrong(quiz, uid)
    bot.delete_message(call.message.chat.id, thinking.message_id)
    bot.send_message(call.message.chat.id, f"🧠 *Deep Explanation:*\n\n{detail}", parse_mode='Markdown')

# ─── FALLBACK ─────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True)
def fallback(message):
    uid = message.from_user.id
    all_users.add(uid)
    bot.reply_to(message,
        f"📚 *{BOT_NAME}* — Subject chuno aur PYQ quiz shuru karo!",
        reply_markup=main_menu(uid), parse_mode='Markdown')

print(f"🚀 {BOT_NAME} — Bihar Board Class 10 PYQ Quiz Bot chal gaya!")
bot.infinity_polling()
