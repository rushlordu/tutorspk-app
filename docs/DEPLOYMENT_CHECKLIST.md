# Deployment checklist

## Required environment variables

- `APP_ENV=production`
- `FLASK_ENV=production`
- `SECRET_KEY` set to a long random value
- `DATABASE_URL` for the production database
- `UPLOAD_DIR` pointing to persistent storage
- `SESSION_COOKIE_SECURE=true` when served over HTTPS
- `MAX_UPLOAD_MB` sized for your expected screenshots/videos

## Media persistence

The app saves uploads to `UPLOAD_DIR` or `uploads/` by default. On many platforms, local files disappear after redeploy. Use a persistent disk, volume, or object storage mount before accepting real tutor profile photos or payment screenshots.

## Dev routes

`/seed` and `/seed-admin` are disabled unless:

```bash
ALLOW_DEV_ROUTES=true
```

Do not enable this in production.

## Smoke test after deployment

1. Register/login as student.
2. Register/login as tutor.
3. Upload tutor profile image from Settings.
4. Reload tutor profile and tutor listing.
5. Submit student payment notice with screenshot.
6. Submit tutor registration fee notice with screenshot.
7. Check admin review pages.
8. Check booking flow and live-session page.
9. Check `/missing-page-test` returns branded 404.
10. Confirm uploads still work after redeploy.
