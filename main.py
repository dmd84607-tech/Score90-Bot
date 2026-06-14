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

# ─── KEYS SECURELY LOAD KARO ─────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "Galat_Token")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "Galat_Key")
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
user_lang      = {}   
user_scores    = {}   
question_count = {}   
subject_stats  = {}   
wrong_answers  = {}   
active_quiz    = {}   
streaks        = {}   

# ─── HELPERS ─────────────────────────────────────────────────
def is_admin(uid):   return str(uid) == ADMIN_ID
def lang(uid):       return user_lang.get(uid, "hl")
def other_lang(uid): return "hi" if lang(uid) == "hl" else "hl"
def lang_label(uid): return "हिंदी (Devanagari)" if lang(uid) == "hi" else "Hinglish (Roman)"
def toggle_label(uid): return "🔡 Hindi Font mein badlo" if lang(uid) == "hl" else "🔤 Hinglish Font mein badlo"

def get_question(q, uid): return q.get("question", q.get("hi" if lang(uid) == "hi" else "hl", ""))
def get_options(q, uid): return q.get("options", q.get("options_hi" if lang(uid) == "hi" else "options_hl", []))
def get_exp(q, uid): return q.get("hint", q.get("exp_hi" if lang(uid) == "hi" else "exp_hl", ""))
def get_correct(q): return q.get("correct_index", q.get("correct", 0))
def get_year(q): return q.get("year", "")
def get_qid(q): return q.get("question", q.get("hi", ""))

def track(uid, subj_key=None, correct=None):
    all_users.add(uid)
    question_count[uid] = question_count.get(uid, 0) + 1
    if subj_key and correct is not None:
        if uid not in subject_stats: subject_stats[uid] = {}
        if subj_key not in subject_stats[uid]: subject_stats[uid][subj_key] = [0, 0]
        subject_stats[uid][subj_key][1] += 1
        if correct: subject_stats[uid][subj_key][0] += 1

def update_streak(uid):
    today = date.today()
    s = streaks.get(uid, {"last_date": None, "count": 0})
    if s["last_date"] == today: return s["count"]
    if s["last_date"] and (today - s["last_date"]).days == 1: s["count"] += 1
    else: s["count"] = 1
    s["last_date"] = today
    streaks[uid] = s
    return s["count"]

def get_streak(uid):
    s = streaks.get(uid, {"last_date": None, "count": 0})
    if s["last_date"] and (date.today() - s["last_date"]).days > 1: return 0
    return s["count"]

def ai_hint(question_text, options):
    if GROQ_API_KEY == "Galat_Key": return "Sawaal ko dhyan se padho aur options compare karo! 🤔"
    opts = "\n".join([f"{['A','B','C','D'][i]}. {o}" for i, o in enumerate(options)])
    prompt = f"Bihar Board Class 10 ke is PYQ sawaal ka sirf ek chhota hint do:\n\nSawaal: {question_text}\n{opts}"
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}], max_tokens=100, temperature=0.5
        )
        return resp.choices[0].message.content.strip()
    except: return "Sawaal ko dhyan se padho aur options compare karo! 🤔"

def ai_explain_wrong(quiz, uid):
    if GROQ_API_KEY == "Galat_Key": return get_exp(quiz, uid)
    q, opts = get_question(quiz, uid), get_options(quiz, uid)
    correct_opt = opts[get_correct(quiz)]
    prompt = f"Bihar Board Class 10 ka yeh PYQ sawaal student ne galat kiya. Simple Hinglish mein samjhao:\n\nSawaal: {q}\nSahi Jawab: {correct_opt}"
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}], max_tokens=300, temperature=0.6
        )
        return resp.choices[0].message.content.strip()
    except: return get_exp(quiz, uid)

# ─── KEYBOARDS ───────────────────────────────────────────────
def main_menu(uid=None):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔬 Science", callback_data="quiz_science"), InlineKeyboardButton("🌍 Social Science", callback_data="quiz_social"))
    markup.row(InlineKeyboardButton("🏆 Score & Stats", callback_data="my_score"), InlineKeyboardButton("🏅 Leaderboard", callback_data="leaderboard"))
    markup.row(InlineKeyboardButton("📝 Review Galat Jawab", callback_data="wrong_review"))
    return markup

def quiz_markup(options, hint_used=False):
    markup = InlineKeyboardMarkup()
    letters = ['A', 'B', 'C', 'D']
    for i, opt in enumerate(options):
        markup.row(InlineKeyboardButton(f"{letters[i]}.  {opt}", callback_data=f"qa_{i}"))
    if not hint_used: markup.row(InlineKeyboardButton("💡 Hint lo", callback_data="get_hint"))
    markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    return markup

def after_quiz_markup(subject_cb):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("▶️ Agla Sawaal", callback_data=subject_cb), InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    markup.row(InlineKeyboardButton("🧠 Aur Detail mein Samjhao", callback_data="explain_more"))
    return markup

def review_markup(idx, total):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("💡 Hint", callback_data=f"review_hint_{idx}"), InlineKeyboardButton("🧠 Detail Samjhao", callback_data=f"review_explain_{idx}"))
    if idx + 1 < total: markup.row(InlineKeyboardButton(f"▶️ Agla ({idx+2}/{total})", callback_data=f"review_next_{idx+1}"))
    markup.row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu"))
    return markup

# ─── COMMANDS & CALLBACKS ────────────────────────────────────
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid, name = message.from_user.id, message.from_user.first_name or "Bhai"
    all_users.add(uid)
    bot.send_message(message.chat.id, f"👑 *Swagat hai Topper, {name}!*\n*{BOT_NAME}* mein aapka aagman ho chuka hai 🎉\n🎯 Mission: Bihar Board mein 400+ Marks!\n\nAaj kaun se subject mein garda udana hai? 👇", reply_markup=main_menu(uid), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data == "main_menu")
def cb_menu(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"📚 *{BOT_NAME}* — Subject chuno", reply_markup=main_menu(call.from_user.id), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data in SUBJECT_BANKS)
def cb_subject(call):
    bot.answer_callback_query(call.id)
    uid, subj_key = call.from_user.id, call.data
    label, bank = SUBJECT_BANKS[subj_key]
    if not bank: 
        bot.send_message(call.message.chat.id, "❌ Is subject ke questions abhi upload nahi hue hain.")
        return
    quiz = random.choice(bank)
    active_quiz[uid] = {**quiz, "subject_cb": subj_key, "_qid": get_qid(quiz), "hint_used": False}
    bot.send_message(call.message.chat.id, f"📚 *{label}*\n\n❓ {get_question(quiz, uid)}", reply_markup=quiz_markup(get_options(quiz, uid), False), parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data.startswith("qa_"))
def cb_answer(call):
    bot.answer_callback_query(call.id)
    uid, quiz = call.from_user.id, active_quiz.get(call.from_user.id)
    if not quiz: return
    chosen, correct = int(call.data.split("_")[1]), get_correct(quiz)
    is_correct = (chosen == correct)
    track(uid, quiz["subject_cb"], is_correct)
    if is_correct: 
        user_scores[uid] = user_scores.get(uid, 0) + 1
        msg = "✅ *Bilkul Sahi!* +1 point 🎉"
    else: 
        msg = f"❌ *Galat!* Sahi tha: *{['A','B','C','D'][correct]}. {get_options(quiz, uid)[correct]}*"
        wr = wrong_answers.setdefault(uid, [])
        if quiz not in wr: wr.append(quiz)
    bot.send_message(call.message.chat.id, f"{msg}\n\n💡 *Explanation:*\n{get_exp(quiz, uid)}", reply_markup=after_quiz_markup(quiz['subject_cb']), parse_mode='Markdown')

# ─── MISSING FUNCTION (COMPLETED) ─────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "wrong_review")
def cb_wrong_review(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    if not wrong_answers.get(uid, []):
        bot.send_message(call.message.chat.id, "🎉 *Mast hai!* Abhi tak koi galat jawab nahi!", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu")), parse_mode='Markdown')
        return
    _send_review_question(call.message.chat.id, uid, 0)

def _send_review_question(chat_id, uid, idx):
    wrongs = wrong_answers.get(uid, [])
    if idx >= len(wrongs):
        bot.send_message(chat_id, "✅ Sab galat sawaal review ho gaye!", reply_markup=InlineKeyboardMarkup().row(InlineKeyboardButton("🏠 Menu", callback_data="main_menu")))
        return
    quiz = wrongs[idx]
    correct_opt = get_options(quiz, uid)[get_correct(quiz)]
    sahi = f"{['A','B','C','D'][get_correct(quiz)]}. {correct_opt}"
    
    bot.send_message(chat_id,
        f"📝 *Galat Jawab Review ({idx+1}/{len(wrongs)})*\n\n"
        f"❓ {get_question(quiz, uid)}\n\n"
        f"✅ Sahi Jawab: *{sahi}*",
        reply_markup=review_markup(idx, len(wrongs)),
        parse_mode='Markdown'
    )

print(f"🚀 {BOT_NAME} Bot Server Start Ho Raha Hai...")
bot.infinity_polling()
