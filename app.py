from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
import sqlite3
from datetime import datetime, timedelta

# ================== APP SETUP ==================
app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# ================== TWILIO CONFIG ==================
# ⚠️ USE YOUR REAL TWILIO CREDENTIALS
TWILIO_SID = "ACbe3c4e53ebf6e84a6babef58c122f71c"
TWILIO_AUTH = "ba2a8cb27670edf104aa5ab5ab30b195"
FROM_WHATSAPP = "whatsapp:+14155238886"   # Twilio sandbox number

# ================== CAREGIVER CONFIG ==================
CARE_GIVER_NUMBER = "+919390401050"   # Caregiver WhatsApp number

client = Client(TWILIO_SID, TWILIO_AUTH)

# ================== DATABASE ==================
def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            phone TEXT,
            medicine TEXT,
            time TEXT,
            status TEXT,
            date TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def insert_record(phone, medicine, time, status):
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO medications VALUES (?, ?, ?, ?, ?)",
        (phone, medicine, time, status, datetime.now().date())
    )
    conn.commit()
    conn.close()

# ================== SEND REMINDER ==================
def send_reminder(phone, medicine):
    try:
        client.messages.create(
            from_=FROM_WHATSAPP,
            to=f"whatsapp:{phone}",
            body=f"⏰ Reminder: Take your medicine *{medicine}*.\nReply TAKEN or MISSED."
        )
        print(f"Reminder sent for {medicine}")
    except Exception as e:
        print("Twilio Error:", e)

# ================== WHATSAPP WEBHOOK ==================
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    raw_msg = request.values.get("Body", "")
    lines = raw_msg.strip().upper().splitlines()
    phone = request.values.get("From").replace("whatsapp:", "")
    response = MessagingResponse()

    for msg in lines:

        # ---------- ADD MEDICINE ----------
        if msg.startswith("ADD"):
            parts = msg.split()

            if len(parts) != 3:
                response.message(
                    f"❌ Format error in:\n{msg}\nUse: ADD <MEDICINE> <HH:MM>"
                )
                continue

            medicine = parts[1]
            time_str = parts[2]

            try:
                now = datetime.now()
                reminder_time = datetime.strptime(time_str, "%H:%M")
                reminder_time = reminder_time.replace(
                    year=now.year,
                    month=now.month,
                    day=now.day,
                    second=0
                )

                # If time already passed → schedule next day
                if reminder_time <= now:
                    reminder_time += timedelta(days=1)

                # UNIQUE JOB ID (prevents overwrite)
                job_id = f"{phone}_{medicine}_{time_str}_{now.timestamp()}"

                scheduler.add_job(
                    send_reminder,
                    'date',
                    run_date=reminder_time,
                    args=[phone, medicine],
                    id=job_id,
                    replace_existing=False
                )

                insert_record(phone, medicine, time_str, "SCHEDULED")
                response.message(f"✅ {medicine} scheduled at {time_str}")

            except ValueError:
                response.message(
                    f"❌ Invalid time in:\n{msg}\nUse HH:MM (24-hour)"
                )

        # ---------- TAKEN ----------
        elif msg == "TAKEN":
            insert_record(phone, "Unknown", "", "TAKEN")
            response.message("✔ Dose recorded as TAKEN")

        # ---------- MISSED (WITH CAREGIVER ALERT) ----------
        elif msg == "MISSED":
            insert_record(phone, "Unknown", "", "MISSED")

            # Notify caregiver
            try:
                client.messages.create(
                    from_=FROM_WHATSAPP,
                    to=f"whatsapp:{CARE_GIVER_NUMBER}",
                    body=(
                        "🚨 Medication Alert!\n"
                        f"Patient: {phone}\n"
                        "Status: MISSED DOSE"
                    )
                )
            except Exception as e:
                print("Caregiver alert error:", e)

            response.message("⚠ Dose MISSED. Caregiver notified")

        # ---------- STATUS ----------
        elif msg == "STATUS":
            conn = sqlite3.connect("database.db")
            c = conn.cursor()

            c.execute(
                "SELECT COUNT(*) FROM medications WHERE phone=? AND status='TAKEN'",
                (phone,)
            )
            taken = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM medications WHERE phone=? AND status!='SCHEDULED'",
                (phone,)
            )
            total = c.fetchone()[0]
            conn.close()

            if total > 0:
                adherence = int((taken / total) * 100)
                response.message(f"📊 Adherence: {adherence}%")
            else:
                response.message("No medication history found")

        # ---------- DAILY REPORT ----------
        elif msg == "REPORT DAILY":
            today = datetime.now().date()

            conn = sqlite3.connect("database.db")
            c = conn.cursor()

            c.execute(
                "SELECT COUNT(*) FROM medications WHERE phone=? AND date=?",
                (phone, today)
            )
            total = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM medications WHERE phone=? AND status='TAKEN' AND date=?",
                (phone, today)
            )
            taken = c.fetchone()[0]
            conn.close()

            if total > 0:
                adherence = int((taken / total) * 100)
                response.message(
                    f"📅 Daily Report ({today})\n"
                    f"Taken: {taken}\n"
                    f"Total: {total}\n"
                    f"Adherence: {adherence}%"
                )
            else:
                response.message("No medication data for today")

        # ---------- WEEKLY REPORT ----------
        elif msg == "REPORT WEEKLY":
            today = datetime.now().date()
            week_start = today - timedelta(days=7)

            conn = sqlite3.connect("database.db")
            c = conn.cursor()

            c.execute(
                "SELECT COUNT(*) FROM medications WHERE phone=? AND date>=?",
                (phone, week_start)
            )
            total = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM medications WHERE phone=? AND status='TAKEN' AND date>=?",
                (phone, week_start)
            )
            taken = c.fetchone()[0]
            conn.close()

            if total > 0:
                adherence = int((taken / total) * 100)
                response.message(
                    "📆 Weekly Report (Last 7 Days)\n"
                    f"Taken: {taken}\n"
                    f"Total: {total}\n"
                    f"Adherence: {adherence}%"
                )
            else:
                response.message("No medication data for this week")

        # ---------- HELP ----------
        else:
            response.message(
                "🤖 Commands:\n"
                "ADD <MEDICINE> <HH:MM>\n"
                "TAKEN\n"
                "MISSED\n"
                "STATUS\n"
                "REPORT DAILY\n"
                "REPORT WEEKLY"
            )

    return str(response)

# ================== RUN SERVER ==================
if __name__ == "__main__":
    app.run(port=5000)
