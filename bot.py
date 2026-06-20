import os
import json
import html
import threading

import gspread
from google.oauth2.service_account import Credentials
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
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
    554392195
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

ASK_NAME, ASK_GOAL, ASK_IMPACT, ASK_NEW_GOAL = range(4)

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
        "Let's set a goal for yourself for the rest of 2026 --\n\n"
        "<b>How do you want to bring an impact to Others around you?</b> 🛟\n\n"
        "<i>Just type it out! I'll bring it up every time you log an impact to keep you on track. (Change it anytime with /setgoal)</i>",
        parse_mode="HTML"
    )

    return ASK_GOAL

async def receive_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal = update.message.text.strip()
    name = context.user_data.get("name", "friend")
    user_id = update.effective_user.id

    try:
        save_to_google_sheet("Users", [name, str(user_id), goal])
        await update.message.reply_text(
            "🎯 GOAL LOCKED IN! \n\n"
            "You are officially registered. Go out there, bring the fire, and make a difference! "
            "Use /impact to log a good deed and /milestones to see what we are chasing together!"
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
    good_deed = update.message.text.strip()
    name = update.effective_user.first_name or "friend"
    user_id = update.effective_user.id

    try:
        save_to_google_sheet("Impacts", [name, str(user_id), good_deed])
        count = get_user_impact_count(user_id)
        goal = get_user_goal(user_id)

        if goal:
            stats_line = (
                f"📊 You've now logged {count} impact(s)!\n"
                f"🎯 Your goal: {goal}"
            )
        else:
            stats_line = (
                f"📊 You've now logged {count} impact(s)!\n"
                "(Tip: run /start to set your personal goal.)"
            )

        await update.message.reply_text(
            "🙌 AMAZING! That's another impact made this week!\n\n"
            f"{stats_line}\n\n"
            "Use /milestones to see how far we've come."
        )

    except Exception as e:
        print(f"[error] could not save to Impacts tab: {e}")
        await update.message.reply_text(
            "Hmm, I had trouble saving that just now. Please try /impact again in a moment."
        )

    return ConversationHandler.END

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
        "Onboarding cancelled. Type /start whenever you're ready to lock in!"
    )

    return ConversationHandler.END

async def milestones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publicly shows the milestones towards the 1000 goal."""
    milestone_text = (
        "🏁 <b>ZONE MILESTONES (Goal: 1000)</b>\n\n"
        "⬜ 250 Impacts: Spark 🪵\n"
        "⬜ 500 Impacts: Campfire 🔥\n"
        "⬜ 750 Impacts: Wildfire 🌲\n"
        "⬜ 1000 Impacts: Inferno 💥"
    )

    await update.message.reply_text(milestone_text, parse_mode="HTML")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays overall zone rankings to authorized users only."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await update.message.reply_text("🔒 This command is restricted to Zone Leaders and Admins.")
        return
    
    leaderboard_text = (
        "🏆 <b>ZONE LEADERBOARD</b> 🏆\n"
        "1. LBE2: 340 impacts\n"
        "2. LBE4: 210 impacts\n\n"
        "Keep pushing towards the 1,000 zone goal!"
    )

    await update.message.reply_text(leaderboard_text, parse_mode="HTML")

async def cg_breakdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays individual breakdown to authorized users only."""
    user_id = update.effective_user.id
    if user_id not in PRIVILEGED_USERS:
        await update.message.reply_text("🔒 You do not have permission to view CG breakdowns.")
        return
    
    cg_text = (
        "👥 <b>LBE2 BREAKDOWN</b>\n"
        "• Micah: 15 impacts (Goal: 30)\n"
        "• Ian: 22 impacts (Goal: 10)\n"
    )

    await update.message.reply_text(cg_text, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the available commands. Leader commands only show for privileged users."""
    user_id = update.effective_user.id
 
    help_text = (
        "🌱 <b>LBE Zone OTHERS Companion</b>\n"
        "<i>Your impact companion for the rest of 2026</i>\n\n"
        "/start — 🔥 Register and set your goal\n"
        "/impact — 🙌 Log a good deed you did for someone\n"
        "/setgoal — 🎯 Update your goal\n"
        "/milestones — 🏁 See our progress towards 1000\n"
        "/cancel — ❌ Cancel whatever's in progress\n"
        "/help — ℹ️ Show all available commands"
    )
 
    # Only privileged users see the leader commands
    if user_id in PRIVILEGED_USERS:
        help_text += (
            "\n"
            "/leaderboard — 🏆 Top zones ranked by impacts\n"
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
            ASK_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_goal)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(onboarding)

    impact_conversation = ConversationHandler(
        entry_points=[CommandHandler("impact", impact_start)],
        states={
            ASK_IMPACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_impact)],
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

    app.add_handler(CommandHandler("milestones", milestones))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("cgbreakdown", cg_breakdown))
    app.add_handler(CommandHandler("help", help_command))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()