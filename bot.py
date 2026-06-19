import os
import json
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
    
}

def save_to_google_sheet(worksheet_name, row):
    """Append one row to the Google Sheet. Reconnects each time (fine at this scale)."""
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet(worksheet_name)
    sheet.append_row(row)

def get_user_impact_count(user_id):
    """Counts how many impacts this user has logged."""
    credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    credentials = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    client = gspread.authorize(credentials)
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

ASK_NAME, ASK_GOAL, ASK_IMPACT = range(3)

web_app = Flask(__name__)
@web_app.route("/")
def home():
    return "Hello, this is our LBE Zone OTHERS bot!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 WELCOME! 🔥\n\n"
        "You are here to make a massive impact in the next half of 2026! "
        "Let's get you set up. First, what is your name or nickname?"
    )

    return ASK_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data["name"] = name
    await update.message.reply_text(
        f"Love it, {name}! Let's make it count. 🚀\n\n"
        "Now, what is your specific goal for the rest of 2026?\n"
        "(e.g., 'I want to impact 50 people' or 'Reach out to 20 people')"
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
        "🔥 Love it! What did you do to make an impact?\n\n"
        "(e.g., 'Made a study care pack for my friends', 'Bought a birthday gift for a friend')"
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
            "🙌 AMAZING! That's another impact logged!\n\n"
            "Every act counts towards our 1000 goal. Keep bringing the fire! 🔥\n\n"
            f"{stats_line}\n\n"
            "Use /milestones to see how far we've come."
        )

    except Exception as e:
        print(f"[error] could not save to Impacts tab: {e}")
        await update.message.reply_text(
            "Hmm, I had trouble saving that just now. Please try /impact again in a moment."
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

    app.add_handler(CommandHandler("milestones", milestones))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("cgbreakdown", cg_breakdown))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()