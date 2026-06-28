# INSIGHT Mobile + Single Device Lock Update

This update adds:

1. A mobile-friendly dashboard layout.
2. Optional access-code login.
3. Optional single-device lock.

## Render Environment Variables

Add these to the INSIGHT Render Web Service:

```text
INSIGHT_ACCESS_CODE=choose-a-private-code
INSIGHT_SINGLE_DEVICE_LOCK=1
INSIGHT_SESSION_DAYS=90
```

Keep your existing variables too:

```text
INSIGHT_AUTOSTART=1
INSIGHT_AUTO_SYMBOLS=15
INSIGHT_INTERVAL=1m
INSIGHT_DEEP_LIMIT=15
INSIGHT_MIN_SCORE=70
```

## Important

A normal website cannot read a visitor's MAC address. Browsers block that for privacy and security.

This app therefore uses a browser-generated device ID stored in localStorage. The first browser/device that enters the correct access code becomes the approved device.

This is good for private sharing and casual access control, but it is not a replacement for a full paid-user licensing system.

## Resetting Device Lock

Local reset:

```powershell
Remove-Item .\insight_device_lock.json -Force
```

On Render, redeploying may reset the lock if the service has no persistent disk. If you add a persistent disk later, delete the lock file from that disk to approve a new device.
