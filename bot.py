import os
import json
import html
import threading

import gspread
from google.oauth2.service_account import Credentials
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    32922725
}

INITIATIVES_COL = {
    "date": 2, "title": 3, "description": 4, "purpose": 5,
    "impact": 6, "time": 7, "venue": 8, "people": 9,
}

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
    sheet = client.open_by_key(SHEET_ID).worksheet("Users")
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
    sheet = client.open_by_key(SHEET_ID).worksheet("Users")
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
    sheet = client.open_by_key(SHEET_ID).worksheet("Users")
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
    sheet = client.open_by_key(SHEET_ID).worksheet("Users")
    rows = sheet.get_all_values()
    # Users columns: Name | Telegram ID | Goal  (Telegram ID = index 1, Goal = index 2)
    for row in rows:
        if len(row) > 2 and row[1] == str(user_id):
            return row[2]
        
    return None

def get_all_initiatives():
    """Return a list of initiative dicts from the Initiatives tab (skips the header row).
    Each dict carries row_num = the real sheet row number, for editing cells in place."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiatives")
    rows = sheet.get_all_values()

    items = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) > 2 and row[2].strip():
            items.append({
                "row_num": i,
                "id": row[0] if len(row) > 0 else "",
                "date": row[1] if len(row) > 1 else "",
                "title": row[2] if len(row) > 2 else "",
                "description": row[3] if len(row) > 3 else "",
                "purpose": row[4] if len(row) > 4 else "",
                "impact": row[5] if len(row) > 5 else "",
                "time": row[6] if len(row) > 6 else "",
                "venue": row[7] if len(row) > 7 else "",
                "people": row[8] if len(row) > 8 else "",
            })

    return items

def add_new_initiative(data):
    """Append a new initiative row. ID is auto-incremented from the highest existing numeric ID."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiatives")
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
        data.get("description", ""),
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
    sheet = client.open_by_key(SHEET_ID).worksheet("Initiatives")
    sheet.update_cell(row_num, INITIATIVES_COL[field_name], new_value)

ASK_NAME, ASK_GOAL, ASK_IMPACT, ASK_NEW_GOAL, ASK_CG, CONFIRM_IMPACT = range(6)
INIT_DATE, INIT_TITLE, INIT_DESC, INIT_PURPOSE, INIT_IMPACT, INIT_TIME, INIT_VENUE, INIT_PEOPLE = range(6, 14)
EDIT_CHOOSE_ROW, EDIT_CHOOSE_FIELD, EDIT_NEW_VALUE = range(14, 17)

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
        save_to_google_sheet("Users", [name, str(user_id), goal, cg])
        await update.message.reply_text(
            "✅ Saved. 🎯 GOAL LOCKED IN!\n\n"
            "Go out there and change lives!\n\n"
            "Use /impact to log an impact <i>(anytime, anywhere)</i> and /milestones to see what we're running towards as a Zone!",
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"[error] could not save to Google Sheet: {e}")
        await update.message.reply_text(
            "Hmm, I had trouble saving that just now. Please try /start again in a moment."
        )

    return ConversationHandler.END

async def impact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 Love it! What impact did you make?\n\n"
        "<i>(you can log the impact as a single text if you have more than 1!)</i>",
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

    await update.message.reply_text(
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
        "✏️ No worries, go ahead and edit your impact. Just send the updated text when you're done.\n\n"
        f"Previous: “{html.escape(previous)}”",
        parse_mode="HTML"
    )

    return ASK_IMPACT

async def setgoal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
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
        await update.message.reply_text(
            "✅ Goal updated!\n\n"
            f"🎯 Your new goal: {new_goal}\n\n"
            "Keep bringing the fire! 🔥"
        )

    except Exception as e:
        print(f"[error] could not update goal: {e}")
        await update.message.reply_text(
            "Hmm, I had trouble updating that just now. Please try /setgoal again in a moment."
        )
 
    return ConversationHandler.END
        

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the onboarding process if they type /cancel."""
    await update.message.reply_text(
        "Action cancelled."
    )

    return ConversationHandler.END

def format_initiatives(items):
    """Build a readable display of all initiatives for the admin."""
    lines = ["📋 <b>WEEKLY INITIATIVES</b>\n"]
    for idx, item in enumerate(items, start=1):
        lines.append(
            f"<b>{idx}. {html.escape(item['title'])}</b>\n"
            f"📅 Date: {html.escape(item['date'])}\n"
            f"⏰ Time: {html.escape(item['time'])}\n"
            f"📍 Venue: {html.escape(item['venue'])}\n"
            f"🎯 Purpose: {html.escape(item['purpose'])}\n"
            f"💥 Impact: {html.escape(item['impact'])}\n"
            f"📝 Description: {html.escape(item['description'])}\n"
            f"👥 People going: {html.escape(item['people'])}"
        )
    return "\n\n".join(lines)
 
async def initiative_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/initiativelist — show outings; if the list is empty, start adding the first one."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await update.message.reply_text("🔒 Oops! This command is for Admins.")
        return ConversationHandler.END
 
    try:
        items = get_all_initiatives()
    except Exception as e:
        print(f"[error] could not read Initiatives tab: {e}")
        await update.message.reply_text("❌ I couldn't read the initiatives sheet. Please try again in a moment.")
        return ConversationHandler.END
 
    if items:
        await update.message.reply_text(format_initiatives(items), parse_mode="HTML")
        return ConversationHandler.END
 
    # Empty list -> begin adding the first outing
    context.user_data["new_init"] = {}
    await update.message.reply_text(
        "📋 <b>No outings yet — let's add the first one!</b>\n\n"
        "📅 What's the <b>date</b> of the outing?",
        parse_mode="HTML"
    )
    return INIT_DATE
 
async def edit_list_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/editlist — list outings and ask which to edit, or 0 to add a new one."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await update.message.reply_text("🔒 Oops! This command is for Admins.")
        return ConversationHandler.END
 
    try:
        items = get_all_initiatives()
    except Exception as e:
        print(f"[error] could not read Initiatives tab: {e}")
        await update.message.reply_text("❌ I couldn't read the initiatives sheet. Please try again in a moment.")
        return ConversationHandler.END
 
    context.user_data["edit_items"] = items
 
    lines = ["✏️ <b>EDIT INITIATIVES</b>\n", "0. ➕ Add a new outing"]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {html.escape(item['title'])} ({html.escape(item['date'])})")
    lines.append("\n<i>Reply with the number you'd like to edit (or 0 to add new). /cancel to exit.</i>")
 
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    return EDIT_CHOOSE_ROW
 
# --- shared "add an outing" flow -------------------------------------------
 
async def init_collect_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["date"] = update.message.text.strip()
    await update.message.reply_text("🏷️ What's the <b>title</b> of the outing?", parse_mode="HTML")
    return INIT_TITLE
 
async def init_collect_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["title"] = update.message.text.strip()
    await update.message.reply_text("📝 Give a short <b>description</b>:", parse_mode="HTML")
    return INIT_DESC
 
async def init_collect_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["description"] = update.message.text.strip()
    await update.message.reply_text("🎯 What's the <b>purpose</b> of this outing?", parse_mode="HTML")
    return INIT_PURPOSE
 
async def init_collect_purpose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["purpose"] = update.message.text.strip()
    await update.message.reply_text("💥 What <b>impact</b> do you hope it makes?", parse_mode="HTML")
    return INIT_IMPACT
 
async def init_collect_impact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["impact"] = update.message.text.strip()
    await update.message.reply_text("⏰ What <b>time</b> is the outing?", parse_mode="HTML")
    return INIT_TIME
 
async def init_collect_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["time"] = update.message.text.strip()
    await update.message.reply_text("📍 What's the <b>venue</b>?", parse_mode="HTML")
    return INIT_VENUE
 
async def init_collect_venue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["venue"] = update.message.text.strip()
    await update.message.reply_text("👥 Who's <b>going</b> for the outing?", parse_mode="HTML")
    return INIT_PEOPLE
 
async def init_collect_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_init"]["people"] = update.message.text.strip()
    data = context.user_data.get("new_init", {})
 
    try:
        add_new_initiative(data)
        await update.message.reply_text(
            "✅ Outing saved!\n\n"
            f"🏷️ {html.escape(data.get('title', ''))}\n"
            f"📅 {html.escape(data.get('date', ''))}\n\n"
            "Use /initiativelist to see the full list.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[error] could not save initiative: {e}")
        await update.message.reply_text("❌ I couldn't save that. Please try /editlist again in a moment.")
 
    context.user_data.pop("new_init", None)
    return ConversationHandler.END
 
# --- edit an existing outing -----------------------------------------------
 
async def edit_choose_row(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    items = context.user_data.get("edit_items", [])
 
    if not text.isdigit() or not (0 <= int(text) <= len(items)):
        await update.message.reply_text(f"❌ Please reply with a number from 0 to {len(items)}.")
        return EDIT_CHOOSE_ROW
 
    choice = int(text)
 
    if choice == 0:
        # add a new outing — reuse the shared add flow
        context.user_data["new_init"] = {}
        await update.message.reply_text(
            "➕ Adding a new outing.\n\n📅 What's the <b>date</b> of the outing?",
            parse_mode="HTML"
        )
        return INIT_DATE
 
    item = items[choice - 1]
    context.user_data["edit_row_num"] = item["row_num"]
    context.user_data["edit_title"] = item["title"]
    await update.message.reply_text(
        f"Editing <b>{html.escape(item['title'])}</b>. Which field would you like to change?\n\n"
        "1. 📅 Date\n"
        "2. 🏷️ Title\n"
        "3. 📝 Description\n"
        "4. 🎯 Purpose\n"
        "5. 💥 Impact\n"
        "6. ⏰ Time\n"
        "7. 📍 Venue\n"
        "8. 👥 People going\n\n"
        "<i>Reply with 1–8:</i>",
        parse_mode="HTML"
    )
    return EDIT_CHOOSE_FIELD
 
async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    field_map = {
        "1": "date", "2": "title", "3": "description", "4": "purpose",
        "5": "impact", "6": "time", "7": "venue", "8": "people",
    }
    if choice not in field_map:
        await update.message.reply_text("❌ Please reply with a number from 1 to 8.")
        return EDIT_CHOOSE_FIELD
 
    context.user_data["edit_field"] = field_map[choice]
    await update.message.reply_text("✏️ Send the new value for that field:")
    return EDIT_NEW_VALUE
 
async def edit_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text.strip()
    row_num = context.user_data.get("edit_row_num")
    field = context.user_data.get("edit_field")
 
    try:
        update_initiative_field(row_num, field, new_value)
        await update.message.reply_text(
            "🎯 Updated! Use /initiativelist to see the changes."
        )
    except Exception as e:
        print(f"[error] could not update initiative: {e}")
        await update.message.reply_text("❌ I couldn't update that. Please try /editlist again in a moment.")
 
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

    await update.message.reply_text(milestone_text, parse_mode="HTML")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays overall zone rankings to authorized users only."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await update.message.reply_text("🔒 Oops! This command is for Admins.")
        return
    
    users = get_all_users()
    counts = get_impact_counts()

    if not users:
        await update.message.reply_text("No data yet — no one has registered.")
        return
    
    cg_totals = {}
    for user in users:
        cg_totals[user["cg"]] = cg_totals.get(user["cg"], 0) + counts.get(user["id"], 0)

    ranked = sorted(cg_totals.items(), key=lambda item: item[1], reverse=True)

    lines = ["🏆 <b>ZONE LEADERBOARD</b> 🏆\n"]
    for rank, (cg, total) in enumerate(ranked, start=1):
        lines.append(f"{rank}. {html.escape(cg)} — {total} {impacts_word(total)}")
    lines.append("\nKeep pushing towards the 1,000 zone goal!")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cg_breakdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays individual breakdown to authorized users only."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await update.message.reply_text("🔒 Oops! This command is for Admins.")
        return
    
    users = get_all_users()
    counts = get_impact_counts()

    if not users:
        await update.message.reply_text("No data yet — no one has registered.")
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

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the available commands. Leader commands only show for privileged users."""
    user_id = update.effective_user.id
 
    help_text = (
        "🌱 <b>LBE Zone OTHERS Companion</b>\n"
        "<i>Your impact companion for the rest of 2026</i>\n\n"
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
            "/leaderboard — 🏆 Top CGs ranked by impacts\n"
            "/cgbreakdown — 👥 Individual breakdown by CG"
        )
 
    help_text += "\n\n<i>Bring the fire. Make a difference. 🔥</i>"
 
    await update.message.reply_text(help_text, parse_mode="HTML")

def main():
    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
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
        entry_points=[CommandHandler("impact", impact_start)],
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
        ],
        states={
            # add-an-outing flow
            INIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_date)],
            INIT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_title)],
            INIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_desc)],
            INIT_PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_purpose)],
            INIT_IMPACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_impact)],
            INIT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_time)],
            INIT_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_venue)],
            INIT_PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, init_collect_people)],
            # edit-an-outing flow
            EDIT_CHOOSE_ROW: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_row)],
            EDIT_CHOOSE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_choose_field)],
            EDIT_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_new_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(initiative_conversation)

    app.add_handler(CommandHandler("milestones", milestones))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("cgbreakdown", cg_breakdown))
    app.add_handler(CommandHandler("help", help_command))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()