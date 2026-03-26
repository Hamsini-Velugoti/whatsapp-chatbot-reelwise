import os
import json
import datetime

from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

app = Flask(__name__)

# --- Configuration ---
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_NUMBER = os.environ["TWILIO_WHATSAPP_NUMBER"]  # e.g. "whatsapp:+14155238886"
MY_WHATSAPP_NUMBER = os.environ["MY_WHATSAPP_NUMBER"]  # e.g. "whatsapp:+91XXXXXXXXXX"
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GOOGLE_SHEETS_CREDS_JSON = os.environ["GOOGLE_SHEETS_CREDS_JSON"]  # JSON string of service account
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "ReelQueue")

# --- Clients ---
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash")

SYSTEM_PROMPT = (
    "You are my personal executive assistant. I will provide Instagram Reels. "
    "Your job is to ignore the fluff and identify the 'Golden Nugget'—the one tool, "
    "book, or habit mentioned. Convert it into a command. For example: "
    "'Set up a Notion Second Brain template' or 'Buy the book Atomic Habits.' "
    "Be direct and brief. Use max 15 words. Return ONLY the action, nothing else."
)


def get_sheets_client():
    """Initialize Google Sheets client from env credentials."""
    creds_dict = json.loads(GOOGLE_SHEETS_CREDS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def get_sheet():
    """Get the ReelQueue worksheet, creating headers if needed."""
    gc = get_sheets_client()
    spreadsheet = gc.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.sheet1
    # Ensure headers exist
    if worksheet.row_count == 0 or worksheet.cell(1, 1).value != "Action":
        worksheet.update("A1:C1", [["Action", "Date", "Status"]])
    return worksheet


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Receive a Reel link via WhatsApp, extract action, save to sheet."""
    sender = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    # Private lock — only respond to my number
    if sender != MY_WHATSAPP_NUMBER:
        return str(MessagingResponse())

    # Ask Gemini for the action
    prompt = f"{SYSTEM_PROMPT}\n\nHere is the Reel link: {body}"
    response = gemini_model.generate_content(prompt)
    action = response.text.strip()

    # Save to Google Sheet
    sheet = get_sheet()
    today = datetime.date.today().isoformat()
    sheet.append_row([action, today, "Pending"])

    # Reply confirmation
    resp = MessagingResponse()
    resp.message(f"✅ Saved: {action}")
    return str(resp)


@app.route("/morning-push", methods=["GET"])
def morning_push():
    """Pick top 3 pending actions, send WhatsApp message, mark as Done."""
    sheet = get_sheet()
    records = sheet.get_all_records()

    # Find pending actions (collect row indices — sheet rows are 1-indexed, +1 for header)
    pending = []
    for i, row in enumerate(records):
        if row.get("Status") == "Pending":
            pending.append({"action": row["Action"], "row_index": i + 2})
        if len(pending) == 3:
            break

    if not pending:
        return "No pending actions.", 200

    # Format message
    lines = ["☀️ Good Morning! Your 3-Step Plan:\n"]
    for idx, item in enumerate(pending, 1):
        lines.append(f"{idx}. {item['action']}")
    message_body = "\n".join(lines)

    # Send via Twilio
    twilio_client.messages.create(
        body=message_body,
        from_=TWILIO_WHATSAPP_NUMBER,
        to=MY_WHATSAPP_NUMBER,
    )

    # Mark as Done
    for item in pending:
        sheet.update_cell(item["row_index"], 3, "Done")

    return f"Sent {len(pending)} actions.", 200


if __name__ == "__main__":
    app.run(debug=True)
