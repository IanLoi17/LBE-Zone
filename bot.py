import os
import json
import html
import re
import datetime
import threading
import asyncio
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SHEET_ID = "1ugX9_qdOCUIgOtA1NfnIlTBlfRsMHSAFfi7BNM167Fk"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PRIVILEGED_USERS = {
    554392195,
    929278147,
    851771524,
    32922725,
    272782991,
    63561597,
    235735470,
    25526066
}

SGT = ZoneInfo("Asia/Singapore")

INITIATIVES_COL = {
    "date": 2, "title": 3, "purpose": 4, "impact": 5, "time": 6, "venue": 7, "people": 8,
}

IMPACT_BUTTON = "🙌 Log an Impact"
OILIST_BUTTON = "📋 O/I List"

USER_KEYBOARD = ReplyKeyboardMarkup([[IMPACT_BUTTON]], resize_keyboard=True)
ADMIN_KEYBOARD = ReplyKeyboardMarkup([[IMPACT_BUTTON, OILIST_BUTTON]], resize_keyboard=True)

PUBLIC_COMMANDS = [
    BotCommand("start", "Register and set your goal"),
    BotCommand("impact", "Log an impact you made for someone"),
    BotCommand("setgoal", "Update your goal"),
    BotCommand("milestones", "See our progress towards 1000"),
    BotCommand("help", "Show all available commands"),
    BotCommand("cancel", "Cancel whatever's in progress"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("initiativelist", "View weekly outings"),
    BotCommand("editlist", "Add or edit an outing"),
    BotCommand("removeinitiative", "Remove an outing"),
    BotCommand("verseotw", "Set or edit the Verse of the Week"),
    BotCommand("announce", "Send an announcement to everyone"),
    BotCommand("leaderboard", "Top CGs ranked by impacts"),
    BotCommand("cgbreakdown", "Individual breakdown by CG"),
]

def keyboard_for(id):
    """Return the right button set for the current user (Zone admins get the O/I List button)"""
    try:
        user_id = int(id)
    except (TypeError, ValueError):
        user_id = id

    return ADMIN_KEYBOARD if user_id in PRIVILEGED_USERS else USER_KEYBOARD

async def reply(update, text, **kwargs):
    """reply_text that always keeps the user's button keyboard attached, so it
    never disappears between messages. Pass reply_markup=... to override for a
    specific message (e.g. the inline confirm buttons)."""
    kwargs.setdefault("reply_markup", keyboard_for(update.effective_user.id))
    return await update.message.reply_text(text, **kwargs)

def save_to_google_sheet(worksheet_name, row):
    """Append one row to the Google Sheet. Reconnects each time (fine at this scale)."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet(worksheet_name)
    sheet.append_row(row)

def update_user_goal(user_id, name, new_goal):
    """Update an existing user's goal in the Users tab, or add them if not found."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Users + Goals")
    rows = sheet.get_all_values()

    # Users columns: Name | Telegram ID | Goal  (Goal is column 3)
    for i, row in enumerate(rows):
        if len(row) > 1 and row[1] == str(user_id):
            sheet.update_cell(i + 1, 3, new_goal)  # i+1 = sheet row number, col 3 = Goal
            return

    # not registered yet — add a new row
    sheet.append_row([name, str(user_id), new_goal])

def get_user_impact_count(user_id):
    """Counts how many impacts this user has logged."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Impacts")
    rows = sheet.get_all_values()

    return sum(1 for row in rows if len(row) > 1 and row[1] == str(user_id))

def get_total_impacts():
    """Counts the TOTAL number of impacts logged by everyone (for the zone progress)."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Impacts")
    rows = sheet.get_all_values()

    # Count data rows only (those with a numeric Telegram ID in column 2; skips the header)
    return sum(1 for row in rows if len(row) > 1 and row[1].isdigit())

def impacts_word(impact_count):
    """Returns the correct singular/plural form of "impact" for a given count."""
    return "impact" if impact_count == 1 else "impacts"

def get_impact_counts():
    """Returns {telegram_id (str): impact_count} for everyone, reading the Impacts tab once."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Impacts")
    rows = sheet.get_all_values()

    counts = {}
    for row in rows:
        if len(row) > 1 and row[1].isdigit():
            counts[row[1]] = counts.get(row[1], 0) + 1

    return counts

def get_all_users():
    """Returns a list of {name, id, goal, cg} for every registered user, reading Users once."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Users + Goals")
    rows = sheet.get_all_values()

    users = []
    for row in rows:
        if len(row) > 1 and row[1].isdigit():
            cg = row[3].strip() if len(row) > 3 and row[3].strip() else "(no CG yet)"
            users.append({
                "name": row[0] if len(row) > 0 else "",
                "id": row[1],
                "goal": row[2] if len(row) > 2 else "",
                "cg": cg,
            })
    return users

def get_user_name(user_id):
    """Return the nickname this user registered with in the Users tab, if any."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Users + Goals")
    rows = sheet.get_all_values()
    for row in rows:
        if len(row) > 1 and row[1] == str(user_id):
            return row[0]

    return None

def get_user_goal(user_id):
    """Look up the goal this user set during /start"""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Users + Goals")
    rows = sheet.get_all_values()
    # Users columns: Name | Telegram ID | Goal  (Telegram ID = index 1, Goal = index 2)
    for row in rows:
        if len(row) > 2 and row[1] == str(user_id):
            return row[2]

    return None

def parse_outing_date(date_str):
    """Turn a free-text date like '26 June' (or '24 June, Wednesday') into a real date so
    the list can be sorted. A trailing day-of-week is stripped before parsing. Unparseable
    text sorts last."""
    s = " ".join((date_str or "").split())
    if not s:
        return datetime.date.max

    # Strip a trailing day-of-week so '24 June, Wednesday' parses like '24 June'.
    weekdays = (r"monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thur|thurs"
                r"|friday|fri|saturday|sat|sunday|sun")
    s = re.sub(rf"[,\s]+(?:{weekdays})\.?$", "", s, flags=re.IGNORECASE).strip()
    s = s.rstrip(",").strip()

    year = datetime.date.today().year
    attempts = [
        (s, "%d %B %Y"), (s, "%d %b %Y"),
        (s, "%B %d %Y"), (s, "%b %d %Y"),
        (s, "%Y-%m-%d"), (s, "%d/%m/%Y"), (s, "%d-%m-%Y"),
        (f"{s} {year}", "%d %B %Y"), (f"{s} {year}", "%d %b %Y"),
        (f"{s} {year}", "%B %d %Y"), (f"{s} {year}", "%b %d %Y"),
        (f"{s}/{year}", "%d/%m/%Y"),
    ]
    for text, fmt in attempts:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return datetime.date.max

def parse_outing_time(time_str):
    """Turn a free-text time like '3.30pm' or '2 PM' into a real time so outings on the
    same day can be ordered. Unparseable text sorts last within its day."""
    s = " ".join((time_str or "").split()).upper().replace(".", ":")
    if not s:
        return datetime.time.max
    attempts = ["%I:%M%p", "%I%p", "%I:%M %p", "%I %p", "%H:%M", "%H%M"]
    for fmt in attempts:
        try:
            return datetime.datetime.strptime(s, fmt).time()
        except ValueError:
            continue

    return datetime.time.max

def get_all_initiatives():
    """Return a list of initiative dicts from the Initiatives tab (skips the header row).
    Each dict carries row_num = the real sheet row number, for editing cells in place."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiative List")
    rows = sheet.get_all_values()

    items = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) > 2 and row[2].strip():
            items.append({
                "row_num": i,
                "id": row[0] if len(row) > 0 else "",
                "date": row[1] if len(row) > 1 else "",
                "title": row[2] if len(row) > 2 else "",
                "purpose": row[3] if len(row) > 3 else "",
                "impact": row[4] if len(row) > 4 else "",
                "time": row[5] if len(row) > 5 else "",
                "venue": row[6] if len(row) > 6 else "",
                "people": row[7] if len(row) > 7 else "",
            })

    items.sort(key=lambda it: (parse_outing_date(it["date"]), parse_outing_time(it["time"])))
    return items

def add_new_initiative(data):
    """Append a new initiative row. ID is auto-incremented from the highest existing numeric ID."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiative List")
    all_rows = sheet.get_all_values()

    max_id = 0
    for row in all_rows[1:]:
        if len(row) > 0 and row[0].strip().isdigit():
            max_id = max(max_id, int(row[0]))
    next_id = max_id + 1

    sheet.append_row([
        str(next_id),
        data.get("date", ""),
        data.get("title", ""),
        data.get("purpose", ""),
        data.get("impact", ""),
        data.get("time", ""),
        data.get("venue", ""),
        data.get("people", ""),
    ])

def update_initiative_field(row_num, field_name, new_value):
    """Update a single cell of an existing initiative (row_num is the real sheet row number)."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiative List")
    sheet.update_cell(row_num, INITIATIVES_COL[field_name], new_value)

def remove_initiative_row(row_num):
    """Delete one outing row, then renumber the ID column so it stays 1, 2, 3..."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiative List")

    sheet.delete_rows(row_num)

    # Renumber the ID column (column A) for the remaining outings, top to bottom.
    rows = sheet.get_all_values()
    new_id = 0
    for i, row in enumerate(rows[1:], start=2):  # skip the header row
        if len(row) > 2 and row[2].strip():      # a non-empty title marks a real outing
            new_id += 1
            if row[0] != str(new_id):
                sheet.update_cell(i, 1, str(new_id))

def get_verseotw():
    """Read the current Verse of the Week from cell A1 of the Verse Of The Week tab."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Verse Of The Week")
    value = sheet.acell("A1").value

    return value.strip() if value else ""

def set_verse(verse):
    """Set a new Verse in cell A1 of the Verse Of The Week tab."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Verse Of The Week")
    sheet.update_acell("A1", verse)

ASK_NAME, ASK_GOAL, ASK_IMPACT, ASK_NEW_GOAL, ASK_CG, CONFIRM_IMPACT = range(6)
INIT_DATE_TITLE, INIT_PURPOSE_IMPACT, INIT_TIME_VENUE, INIT_PEOPLE = range(6, 10)
EDIT_CHOOSE_ROW, EDIT_CHOOSE_FIELD, EDIT_NEW_VALUE = range(10, 13)
REMOVE_INITIATIVE = 13
ASK_VERSE_OTW = 14
ASK_ANNOUNCE, CONFIRM_ANNOUNCE = range(15, 17)

web_app = Flask(__name__)
@web_app.route("/")
def home():
    return "Hello, this is our LBE Zone OTHERS bot!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey there! 🤟 Ready to make an impact and reach Others? I'm here to help you out!\n\n"
        "This next half of 2026 - it's an opportunity for you Make A Difference in someone else's life! Set a goal, and stay faithful to it!\n\n"
        "Each week I'll help you track your progress towards it. 😊\n\n"
        "Before we begin, how shall I address you?\n"
        "<i>Type a nickname to get started — or /cancel if you're not ready yet.</i>",
        parse_mode="HTML"
    )

    return ASK_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data["name"] = name
    await update.message.reply_text(
        f"Gotcha, {html.escape(name)}! 👋\n\n"
        "<b>Which CG do you belong to?</b>\n"
        "<i>Just type it out — e.g. LBE2</i>",
        parse_mode="HTML"
    )

    return ASK_CG

async def receive_cg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cg = update.message.text.strip().upper()
    context.user_data["CG"] = cg
    await update.message.reply_text(
        "Got it! 🙌\n\n"
        "Now let's set a goal for yourself for the rest of 2026 --\n\n"
        "<b>How do you want to bring an impact to Others around you?</b> 🛟\n\n"
        "<i>Just type it out! I'll bring it up every time you log an impact to keep you on track. (Change it anytime with /setgoal)</i>",
        parse_mode="HTML"
    )

    return ASK_GOAL

async def receive_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal = update.message.text.strip()
    name = context.user_data.get("name", "friend")
    cg = context.user_data.get("CG", "")
    user_id = update.effective_user.id

    try:
        save_to_google_sheet("Users + Goals", [name, str(user_id), goal, cg])
        await reply(update, 
            "✅ Saved. 🎯 GOAL LOCKED IN!\n\n"
            "Go out there and change lives!\n\n"
            "Use /impact to log an impact <i>(anytime, anywhere)</i> and /milestones to see what we're running towards as a Zone!\n\n"
            "Pro-Tip: Use /help to see all other available commands!",
            parse_mode="HTML",
            reply_markup=keyboard_for(user_id)
        )

    except Exception as e:
        print(f"[error] could not save to Google Sheet: {e}")
        await reply(update, 
            "Hmm, I had trouble saving that just now. Please try /start again in a moment."
        )

    return ConversationHandler.END

async def impact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, 
        "🔥 Love it! What impact did you make?\n\n"
        "<i>(If you made multiple impacts, you can log each one separately using the same command.)</i>",
        parse_mode="HTML"
    )

    return ASK_IMPACT

async def receive_impact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pending_impact"] = update.message.text.strip()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, log it", callback_data="impact_confirm"),
            InlineKeyboardButton("✏️ Keep editing", callback_data="impact_edit")
        ]
    ])

    await reply(update, 
        "📝 Just to confirm — is this what you'd like to log?\n\n"
        f"“{html.escape(context.user_data['pending_impact'])}”\n\n"
        "Tap ✅ Yes, log it if it's correct, or ✏️ Keep editing to rewrite it.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    return CONFIRM_IMPACT

async def confirm_impact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    impact = context.user_data.get("pending_impact", "")
    user_id = update.effective_user.id
    name = get_user_name(user_id) or update.effective_user.first_name or "friend"

    try:
        save_to_google_sheet("Impacts", [name, str(user_id), impact])
        count = get_user_impact_count(user_id)
        goal = get_user_goal(user_id)

        if goal:
            stats_line = (
                f"📊 You've now logged {count} {impacts_word(count)}!\n"
                f"🎯 Your goal: {goal}"
            )
        else:
            stats_line = (
                f"📊 You've now logged {count} {impacts_word(count)}!\n"
                "(Tip: run /start to set your personal goal.)"
            )

        await query.edit_message_text(
            "🙌 AMAZING! That's another impact made this week!\n\n"
            f"{stats_line}\n\n"
            "Use /milestones to see how far we've come."
        )

    except Exception as e:
        print(f"[error] could not save to Impacts tab: {e}")
        await query.edit_message_text(
            "Hmm, I had trouble saving that just now. Please try /impact again in a moment."
        )

    return ConversationHandler.END

async def edit_impact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    previous = context.user_data.get("pending_impact", "")
    await query.edit_message_text(
        "✏️ No worries, copy and paste your previous response below, make any edits you'd like, and send it back when you're done.\n\n"
        f"Previous: “{html.escape(previous)}”",
        parse_mode="HTML"
    )

    return ASK_IMPACT

async def setgoal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, 
        "🎯 Let's update your goal!\n\n"
        "What's your goal for the rest of 2026?\n"
        "<i>Just type it out — or /cancel to keep your current one.</i>",
        parse_mode="HTML"
    )

    return ASK_NEW_GOAL

async def receive_new_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_goal = update.message.text.strip()
    name = update.effective_user.first_name or "friend"
    user_id = update.effective_user.id

    try:
        update_user_goal(user_id, name, new_goal)
        await reply(update, 
            "✅ Goal updated!\n\n"
            f"🎯 Your new goal: {new_goal}\n\n"
            "Keep bringing the fire! 🔥"
        )

    except Exception as e:
        print(f"[error] could not update goal: {e}")
        await reply(update, 
            "Hmm, I had trouble updating that just now. Please try /setgoal again in a moment."
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the onboarding process if they type /cancel."""
    await reply(update, 
        "Action cancelled."
    )

    return ConversationHandler.END

def split_two(text):
    """Split a message into two fields. Prefers a | separator, falls back to a newline.
    Returns a list of 1 or 2 stripped parts."""
    if "|" in text:
        parts = text.split("|", 1)
    elif "\n" in text:
        parts = text.split("\n", 1)
    else:
        parts = [text]
    return [p.strip() for p in parts]

def format_initiatives(items):
    """Build a readable display of all initiatives for the admin."""
    lines = [
        "📋 <b>WEEKLY INITIATIVES</b>\n",
        "<b>Leading Questions:</b>\n"
        "💭 Who am I putting before myself this week?\n"
        "💭 How can I make time for this person/these people?\n"
        ]
    for idx, item in enumerate(items, start=1):
        lines.append(
            f"<b>{idx}. {html.escape(item['title'])}</b>\n"
            f"📅 Date: {html.escape(item['date'])}\n"
            f"⏰ Time: {html.escape(item['time'])}\n"
            f"📍 Venue: {html.escape(item['venue'])}\n"
            f"🎯 Purpose: {html.escape(item['purpose'])}\n"
            f"💥 Impact: {html.escape(item['impact'])}\n"
            f"👥 People going: {html.escape(item['people'])}"
        )
    return "\n\n".join(lines)

async def initiative_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/initiativelist — show outings; if the list is empty, start adding the first one."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return ConversationHandler.END

    try:
        items = get_all_initiatives()
    except Exception as e:
        print(f"[error] could not read Initiatives tab: {e}")
        await reply(update, "❌ I couldn't read the initiatives sheet. Please try again in a moment.")
        return ConversationHandler.END

    if items:
        await reply(update, format_initiatives(items), parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["new_init"] = {}
    await reply(update, 
        "📋 <b>No outings yet — let's add the first one!</b>\n\n"
        "What is the <b>date</b> + <b>day</b> and <b>title</b> of the outing? Please follow the example below.\n\n"
        "<i>Example:\n29 June, Monday | XX with XX</i>",
        parse_mode="HTML"
    )
    return INIT_DATE_TITLE

async def edit_list_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/editlist — list outings and ask which to edit, or 0 to add a new one."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return ConversationHandler.END

    try:
        items = get_all_initiatives()
    except Exception as e:
        print(f"[error] could not read Initiatives tab: {e}")
        await reply(update, "❌ I couldn't read the initiatives sheet. Please try again in a moment.")
        return ConversationHandler.END

    context.user_data["edit_items"] = items

    lines = ["✏️ <b>EDIT INITIATIVES</b>\n", "0. ➕ Add a new outing"]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {html.escape(item['title'])} ({html.escape(item['date'])})")
    lines.append("\n<i>Reply with the number you'd like to edit (or 0 to add new). /cancel to exit.</i>")

    await reply(update, "\n".join(lines), parse_mode="HTML")
    return EDIT_CHOOSE_ROW

async def remove_list_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/removeinitiative — list outings and ask which number to delete (admins only)."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return ConversationHandler.END

    try:
        items = get_all_initiatives()
    except Exception as e:
        print(f"[error] could not read Initiatives tab: {e}")
        await reply(update, "❌ I couldn't read the initiatives sheet. Please try again in a moment.")
        return ConversationHandler.END

    if not items:
        await reply(update, "📋 There are no outings to remove.")
        return ConversationHandler.END

    context.user_data["remove_items"] = items

    lines = ["🗑️ <b>REMOVE AN OUTING</b>\n"]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {html.escape(item['title'])} ({html.escape(item['date'])})")
    lines.append("\n<i>Reply with the number to remove. /cancel to exit.</i>")

    await reply(update, "\n".join(lines), parse_mode="HTML")
    return REMOVE_INITIATIVE

# --- shared "add an outing" flow (4 prompts) -------------------------------

async def init_collect_date_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = split_two(update.message.text)
    if len(parts) < 2 or not parts[1]:
        await reply(update, 
            "❌ Please enter both the date + day and title of the outing, separated by a |\n\n"
            "<i>Example: 29 June, Monday | XX with XX</i>",
            parse_mode="HTML"
        )
        return INIT_DATE_TITLE

    context.user_data["new_init"]["date"] = parts[0]
    context.user_data["new_init"]["title"] = parts[1]
    await reply(update, 
        "🎯 What is the <b>purpose</b> and <b>impact</b> of this outing? Please follow the example below.\n\n"
        "<i>Example:\nContinue building r/s with XX | Inspire XX to...</i>",
        parse_mode="HTML"
    )
    return INIT_PURPOSE_IMPACT

async def init_collect_purpose_impact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = split_two(update.message.text)
    if len(parts) < 2 or not parts[1]:
        await reply(update, 
            "❌ Please enter both the purpose and impact of the outing, separated by a |\n\n"
            "<i>Example: Continue building r/s with XX | Inspire XX to...</i>",
            parse_mode="HTML"
        )
        return INIT_PURPOSE_IMPACT

    context.user_data["new_init"]["purpose"] = parts[0]
    context.user_data["new_init"]["impact"] = parts[1]
    await reply(update, 
        "⏰ What is the <b>time</b> and <b>venue</b> of the outing? Please follow the example below.\n\n"
        "<i>Example:\n2 PM | Location</i>",
        parse_mode="HTML"
    )
    return INIT_TIME_VENUE

async def init_collect_time_venue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = split_two(update.message.text)
    if len(parts) < 2 or not parts[1]:
        await reply(update, 
            "❌ Please enter both the time and the venue, separated by a |\n\n"
            "<i>Example: 2 PM | Location</i>",
            parse_mode="HTML"
        )
        return INIT_TIME_VENUE

    context.user_data["new_init"]["time"] = parts[0]
    context.user_data["new_init"]["venue"] = parts[1]
    await reply(update, "👥 Lastly, who's <b>going</b> for the outing?", parse_mode="HTML")
    return INIT_PEOPLE

async def init_collect_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["people"] = update.message.text.strip()
    data = context.user_data.get("new_init", {})

    try:
        add_new_initiative(data)
        await reply(update, 
            "✅ Outing saved!\n\n"
            f"🏷️ {html.escape(data.get('title', ''))}\n"
            f"📅 {html.escape(data.get('date', ''))}\n\n"
            "Use /initiativelist to see the full list, /editlist to edit or add outings, or /removeinitiative to remove an outing.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[error] could not save initiative: {e}")
        await reply(update, "❌ I couldn't save that. Please try /editlist again in a moment.")

    context.user_data.pop("new_init", None)
    return ConversationHandler.END

# --- edit an existing outing -----------------------------------------------

async def edit_choose_row(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    items = context.user_data.get("edit_items", [])

    if not text.isdigit() or not (0 <= int(text) <= len(items)):
        await reply(update, f"❌ Please reply with a number from 0 to {len(items)}.")
        return EDIT_CHOOSE_ROW

    choice = int(text)

    if choice == 0:
        context.user_data["new_init"] = {}
        await reply(update, 
            "➕ Adding a new outing.\n\n"
            "What is the <b>date</b> + <b>day</b> and <b>title</b> of the outing? Please follow the example below.\n\n"
            "<i>Example:\n29 June, Monday | XX with XX</i>",
            parse_mode="HTML"
        )
        return INIT_DATE_TITLE

    item = items[choice - 1]
    context.user_data["edit_row_num"] = item["row_num"]
    context.user_data["edit_title"] = item["title"]
    await reply(update, 
        f"Editing <b>{html.escape(item['title'])}</b>. Which field would you like to change?\n\n"
        "1. 📅 Date\n"
        "2. 🏷️ Title\n"
        "3. 🎯 Purpose\n"
        "4. 💥 Impact\n"
        "5. ⏰ Time\n"
        "6. 📍 Venue\n"
        "7. 👥 People going\n\n"
        "<i>Reply with 1–7:</i>",
        parse_mode="HTML"
    )
    return EDIT_CHOOSE_FIELD

async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    field_map = {
        "1": "date", "2": "title", "3": "purpose", "4": "impact",
        "5": "time", "6": "venue", "7": "people",
    }
    if choice not in field_map:
        await reply(update, "❌ Please reply with a number from 1 to 7.")
        return EDIT_CHOOSE_FIELD

    context.user_data["edit_field"] = field_map[choice]
    await reply(update, "✏️ Send the new value for that field:")
    return EDIT_NEW_VALUE

async def edit_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text.strip()
    row_num = context.user_data.get("edit_row_num")
    field = context.user_data.get("edit_field")

    try:
        update_initiative_field(row_num, field, new_value)
        await reply(update, "🎯 Updated! Use /initiativelist to see the changes.")
    except Exception as e:
        print(f"[error] could not update initiative: {e}")
        await reply(update, "❌ I couldn't update that. Please try /editlist again in a moment.")
    return ConversationHandler.END

async def remove_initiative(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    items = context.user_data.get("remove_items", [])

    if not text.isdigit() or not (1 <= int(text) <= len(items)):
        await reply(update, f"❌ Please reply with a number from 1 to {len(items)}.")
        return REMOVE_INITIATIVE

    item = items[int(text) - 1]
    try:
        remove_initiative_row(item["row_num"])
        await reply(update, 
            f"🗑️ Removed <b>{html.escape(item['title'])}</b> ({html.escape(item['date'])}).\n\n"
            "The remaining outings have been renumbered. Use /initiativelist to see the updated list.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[error] could not remove initiative: {e}")
        await reply(update, "❌ I couldn't remove that. Please try /removeinitiative again in a moment.")

    return ConversationHandler.END

async def verseotw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/verseotw for admin to set for the upcoming week"""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return ConversationHandler.END

    await reply(update, 
        "📖 Send me the <b>Verse of the Week</b>.\n\n"
        "<i>For example:\n1 John 4:7 (NIV): — Dear friends, let us love one another, for love comes from God. Everyone who loves has been born of God and knows God.</i>\n\n"
        "To change the verse later, just run the command /verseotw again.\n"
        "Or /cancel to keep the current one.",
        parse_mode="HTML"
    )

    return ASK_VERSE_OTW

async def receive_verseotw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the Verse of the week to the google sheets."""
    verseotw = update.message.text.strip()
    try:
        set_verse(verseotw)
        await reply(update, 
            "✅ Verse of the Week saved!\n\n"
            f"📖 This week's verse: {html.escape(verseotw)}",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[error] could not save verse: {e}")
        await reply(update, "❌ I couldn't save that. Please try /verseotw again in a moment.")

    return ConversationHandler.END

async def post_verseotw(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue callback - runs Monday and DMs the verse to every registered user."""
    try:
        verseotw = get_verseotw()
    except Exception as e:
        print(f"[verseotw] could not read verse of the week: {e}")
        return

    if not verseotw:
        print("[verseotw] no verse of the week set.")
        return

    try:
        users = get_all_users()
    except Exception as e:
        print(f"[verseotw] could not read users: {e}")
        return

    message = (
        "📖 <b>VERSE OF THE WEEK</b> 🌱\n\n"
        f"{html.escape(verseotw)}\n\n"
        "<i>Stay encouraged, and keep making an impact this week! 🛟</i>"
    )

    sent, failed = 0, 0
    seen_ids = set()
    for user in users:
        user_id = user["id"]

        if user_id in seen_ids:
            continue

        seen_ids.add(user_id)
        try:
            await context.bot.send_message(chat_id=int(user_id), text=message, parse_mode="HTML")
            sent += 1
        except Exception as e:
            # Most common cause: the user blocked the bot or never started a chat with it.
            failed += 1
            print(f"[verseotw] could not DM {user_id}: {e}")

        await asyncio.sleep(0.05)

    print(f"[verseotw] weekly verse sent to {sent} user(s); {failed} failed.")

async def announce_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/announce command for Zone admins only. Sends a one-off announcement to every registered user."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return ConversationHandler.END

    await reply(update, 
        "📢 What announcement would you like to send to <b>everyone</b>?\n\n"
        "<i>Type your message, or /cancel to stop.</i>",
        parse_mode="HTML"
    )

    return ASK_ANNOUNCE

async def receive_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the announcement and show a preview to confirm before broadcasting."""
    text = update.message.text
    context.user_data["announce_text"] = text

    try:
        users = get_all_users()
    except Exception as e:
        print(f"[announce] could not read users: {e}")
        await reply(update, "❌ I couldn't read the user list. Please try /announce again in a moment.")
        return ConversationHandler.END

    recipient_count = len({user["id"] for user in users})
    context.user_data["announce_count"] = recipient_count

    preview = f"📢 <b>ANNOUNCEMENT</b>\n\n{text}"
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"📤 Send to all {recipient_count}", callback_data="announce_send"),
            InlineKeyboardButton("❌ Cancel", callback_data="announce_cancel"),
        ]
    ])

    try:
        await reply(update, 
            f"{preview}\n\n———\n<i>Preview above. Send this to everyone?</i>",
            parse_mode="HTML",
            reply_markup=buttons,
        )
    except Exception as e:
        print(f"[announce] preview failed (likely bad formatting): {e}")
        await reply(update, 
            "❌ I couldn't format that message — please try sending it again."
        )
        return ASK_ANNOUNCE

    return CONFIRM_ANNOUNCE

async def announce_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast the announcement to every registered user."""
    query = update.callback_query
    await query.answer()

    text = context.user_data.get("announce_text", "")
    if not text:
        await query.edit_message_text("Nothing to send.")
        return ConversationHandler.END

    try:
        users = get_all_users()
    except Exception as e:
        print(f"[announce] could not read users: {e}")
        await query.edit_message_text("❌ I couldn't read the user list. Please try /announce again.")
        return ConversationHandler.END

    message = f"📢 <b>ANNOUNCEMENT</b>\n\n{text}"
    await query.edit_message_text("📤 Sending announcement...")

    sent, failed = 0, 0
    seen_ids = set()
    for user in users:
        user_id = user["id"]

        if user_id in seen_ids:
            continue

        seen_ids.add(user_id)
        try:
            # Attaching the keyboard here also delivers the buttons to existing users.
            # Admins get the extra O/I List button; everyone else gets just Impact.
            await context.bot.send_message(
                chat_id=int(user_id), text=message, parse_mode="HTML", reply_markup=keyboard_for(user_id)
            )
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[announce] could not DM {user_id}: {e}")

        await asyncio.sleep(0.05)

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ Announcement sent to {sent} people. ({failed} couldn't be reached.)"
    )

    context.user_data.pop("announce_text", None)
    context.user_data.pop("announce_count", None)

    return ConversationHandler.END

async def announce_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Abort the announcement without sending anything."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("announce_text", None)
    context.user_data.pop("announce_count", None)
    await query.edit_message_text("Announcement cancelled. Nothing was sent.")
    return ConversationHandler.END

async def milestones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the live total progress towards 1000 impacts"""
    total = get_total_impacts()
    percent = round(total / 1000 * 100)

    milestone_text = (
        "🏁 <b>Goal: 1000 Impacts</b>\n\n"
        f"➡️ <i>Current Progress: {total}/1000 ({percent}%)</i>\n\n"
        "Milestones:\n"
        "50 Impacts: 🙏🏻\n"
        "100 Impacts: ✨\n"
        "200 Impacts: 🔥\n"
        "350 Impacts: 📛\n"
        "500 Impacts: 🧨\n"
        "650 Impacts: 💥\n"
        "800 Impacts: 🎆\n"
        "1000 Impacts: 🏁"
    )

    await reply(update, milestone_text, parse_mode="HTML")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays overall zone rankings to authorized users only."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return

    users = get_all_users()
    counts = get_impact_counts()

    if not users:
        await reply(update, "No data yet — no one has registered.")
        return

    cg_totals = {}
    for user in users:
        cg_totals[user["cg"]] = cg_totals.get(user["cg"], 0) + counts.get(user["id"], 0)

    ranked = sorted(cg_totals.items(), key=lambda item: item[1], reverse=True)

    lines = ["🏆 <b>ZONE LEADERBOARD</b> 🏆\n"]
    for rank, (cg, total) in enumerate(ranked, start=1):
        lines.append(f"{rank}. {html.escape(cg)} — {total} {impacts_word(total)}")
    lines.append("\nKeep pushing towards the 1,000 zone goal!")

    await reply(update, "\n".join(lines), parse_mode="HTML")

async def cg_breakdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays individual breakdown to authorized users only."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await reply(update, "🔒 Oops! This command is for Admins.")
        return

    users = get_all_users()
    counts = get_impact_counts()

    if not users:
        await reply(update, "No data yet — no one has registered.")
        return

    cg_members = {}
    for user in users:
        cg_members.setdefault(user["cg"], []).append(user)

    lines = ["👥 <b>CG BREAKDOWN</b>\n"]
    for cg in sorted(cg_members.keys()):
        lines.append(f"<b>{html.escape(cg)}</b>")
        members = sorted(cg_members[cg], key=lambda u: counts.get(u["id"], 0), reverse=True)

        for user in members:
            count = counts.get(user["id"], 0)
            lines.append(f"• {html.escape(user['name'])}: {count}")

        lines.append("")

    await reply(update, "\n".join(lines), parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the available commands. Leader commands only show for privileged users."""
    user_id = update.effective_user.id

    help_text = (
        "🌱 <b>LBE Zone OTHERS Companion</b>\n"
        "<i>Your friendly neighborhood impact companion!</i> 🤓\n\n"
        "/start — 🔥 Register and set your goal\n"
        "/impact — 🙌 Log an impact you made for someone\n"
        "/setgoal — 🎯 Update your goal\n"
        "/milestones — 🏁 See our progress towards 1000\n"
        "/cancel — ❌ Cancel whatever's in progress\n"
        "/help — ℹ️ Show all available commands"
    )

    # Only privileged users see the leader commands
    if user_id in PRIVILEGED_USERS:
        help_text += (
            "\n"
            "/initiativelist — 📋 View weekly outings (or add the first if empty)\n"
            "/editlist — ✏️ Add or edit an outing\n"
            "/removeinitiative — 🗑️ Remove an outing\n"
            "/verseotw — 📖 Set or edit the Verse of the Week\n"
            "/announce — 📢 Send an announcement to everyone\n"
            "/leaderboard — 🏆 Top CGs ranked by impacts\n"
            "/cgbreakdown — 👥 Individual breakdown by CG"
        )

    await reply(update, help_text, parse_mode="HTML")

async def post_init(app):
    await app.bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in PRIVILEGED_USERS:
        try:
            await app.bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            print(f"[commands] could not set admin menu for {admin_id}: {e}")

def main():
    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            ASK_CG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cg)],
            ASK_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_goal)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(onboarding)

    impact_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("impact", impact_start),
            MessageHandler(filters.Regex("^🙌 Log an Impact$"), impact_start),
        ],
        states={
            ASK_IMPACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_impact)],
            CONFIRM_IMPACT: [
                CallbackQueryHandler(confirm_impact, pattern="^impact_confirm$"),
                CallbackQueryHandler(edit_impact, pattern="^impact_edit$"),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(impact_conversation)

    setgoal_conversation = ConversationHandler(
        entry_points=[CommandHandler("setgoal", setgoal_start)],
        states={
            ASK_NEW_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_goal)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(setgoal_conversation)

    initiative_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("initiativelist", initiative_list),
            CommandHandler("editlist", edit_list_start),
            CommandHandler("removeinitiative", remove_list_start),
            MessageHandler(filters.Regex("^📋 O/I List$"), initiative_list),
        ],
        states={
            # add-an-outing flow (4 prompts)
            INIT_DATE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_date_title)],
            INIT_PURPOSE_IMPACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_purpose_impact)],
            INIT_TIME_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_time_venue)],
            INIT_PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_people)],
            # edit-an-outing flow
            EDIT_CHOOSE_ROW: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_row)],
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_value)],

            REMOVE_INITIATIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_initiative)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(initiative_conversation)

    verse_conversation = ConversationHandler(
        entry_points=[CommandHandler("verseotw", verseotw_start)],
        states={
            ASK_VERSE_OTW: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_verseotw)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(verse_conversation)

    announce_conversation = ConversationHandler(
        entry_points=[CommandHandler("announce", announce_start)],
        states={
            ASK_ANNOUNCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_announce)],
            CONFIRM_ANNOUNCE: [
                CallbackQueryHandler(announce_send, pattern="^announce_send$"),
                CallbackQueryHandler(announce_cancel, pattern="^announce_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(announce_conversation)

    app.add_handler(CommandHandler("milestones", milestones))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("cgbreakdown", cg_breakdown))
    app.add_handler(CommandHandler("help", help_command))

    # Weekly Verse of the Week — sent every Monday at 12:00 noon SGT.
    # In PTB v20+, days use 0=Sunday..6=Saturday, so Monday = 1.
    app.job_queue.run_daily(
        post_verseotw,
        time=datetime.time(hour=12, minute=0, tzinfo=SGT),
        days=(1,),
        name="verse_of_the_week"
    )

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
