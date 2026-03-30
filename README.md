# TutorPK Live Admin MVP

A Flask + SQLite tutoring marketplace for Pakistan with:
- in-platform live classes
- transparent admin observer mode
- manual credit top-ups through bank transfer
- chat scanning for phone numbers, emails, WhatsApp, and off-platform contact attempts
- admin review for payment notices and tutor withdrawals

## Core mechanics implemented

### Credits and payments
- `bank_details.txt` stores the bank title, IBAN, admin email, WhatsApp number, and PKR-to-credit rate
- students buy credits at **PKR 10 = 1 credit**
- payment screen shows the bank title + IBAN + admin WhatsApp
- student submits a payment notice after transfer
- admin receives an email notification if SMTP is configured; otherwise the email is logged to `email_outbox.log`
- admin manually approves the payment notice and credits the student account
- bookings hold credits in escrow until the student marks the session complete
- once the student marks complete, credits move to the tutor earnings balance
- tutor withdrawal requests are reviewed and marked paid manually by admin

### Admin controls
- admin account can see all users, payment notices, withdrawals, moderation flags, bookings, and live rooms
- admin can manually adjust user credits
- admin can join any live room as an observer
- when admin joins a room, a visible observer notice appears to all participants

### Moderation and platform protection
- chat blocks emails, phone numbers, WhatsApp mentions, and similar contact-sharing attempts
- blocked messages are logged in admin moderation flags
- room UI clearly states that admin observation is transparent

## Quick start

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`

## Demo data

Visit `/seed`

- Admin: `jojopk44@gmail.com` / `admin123`
- Student: `hamza@example.com` / `password123`
- Tutor: `ayesha@example.com` / `password123`

## Important notes

- Database file is `tutorpk_live_admin.db`
- If you used an older TutorPK version, this new DB name avoids schema conflicts
- For Gmail SMTP, you should configure environment variables such as `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and optionally `SMTP_SENDER`
- This demo uses STUN only. For reliable calls in Pakistan, add a TURN server such as coturn
