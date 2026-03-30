# TutorPK Full Redesign Build

This build implements the features that can be shipped now without external credentials, and scaffolds the ones that need later configuration.

## Included now
- redesigned homepage
- featured tutors and 10 rich demo tutor profiles
- student / tutor / admin accounts
- manual bank-transfer credit purchase flow
- visible bank details and WhatsApp proof instruction
- admin payment notice review panel
- student credits wallet and ledger
- tutor earnings, pending payout, withdrawal requests
- 20% commission logic with tutor-side bonus engine
- milestone, activity, and rating bonuses
- tutor feedback system
- alias/public names, qualification-only display, no institution, no current role
- admin dashboard, user management, tutor verification, live session monitor
- transparent admin observer banner in live sessions
- protected chat shell with phone/email/WhatsApp scanning
- Google login scaffolding
- SMTP / email notification scaffolding with log fallback

## Run
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open the local URL shown by Flask.

## Seed demo data
Visit `/seed`

Demo logins:
- admin: `jojopk44@gmail.com` / `admin123`
- student: `hamza@example.com` / `password123`
- demo tutors: any demo tutor email / `password123`

## What still needs external configuration later
- real SMTP delivery: set MAIL_* variables
- real Google login: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
- production-grade video backend / TURN / recording
- real automated JazzCash / EasyPaisa / bank verification
- real payout automation

## Notes
This build uses a monitored live-session shell rather than a production media stack. It is meant as a strong product MVP and admin-control base.
