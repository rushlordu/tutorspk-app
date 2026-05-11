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

## Current improvement pass

This package adds a safer production/UI pass on top of the previous fixes:

- modernized tutor settings/profile layout
- live profile-image preview before saving
- validated profile image, demo video, payment screenshot, and qualification uploads
- uploaded media organized into safer subfolders
- missing/broken image fallback remains active
- session cookie/security header hardening
- safer login redirect handling
- lightweight session-based login throttling
- friendly 403, 404, 413, and 500 pages
- configurable upload size via `MAX_UPLOAD_MB`
- seed/dev routes disabled by default unless `ALLOW_DEV_ROUTES=true`
- `wsgi.py` and `gunicorn.conf.py` for deployment entrypoints

## Run locally

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

For local debug mode:

```bash
set FLASK_DEBUG=true
python app.py
```

## Seed demo data

Seed routes are now disabled by default for safety.

To use them in a safe local environment only, set:

```bash
ALLOW_DEV_ROUTES=true
```

Then visit `/seed` while logged in as admin.

## Production notes

Set at minimum:

```bash
APP_ENV=production
FLASK_ENV=production
SECRET_KEY=<long-random-secret>
SESSION_COOKIE_SECURE=true
DATABASE_URL=<production-database-url>
UPLOAD_DIR=<persistent-upload-directory>
```

Important: profile images and screenshots must be stored in persistent storage. Many hosting providers erase local files on redeploy unless you configure a persistent disk/bucket.
