---
name: Bug Report
about: Something is not working correctly
title: "fix: "
labels: bug
assignees: ""
---

## Description

A clear description of the bug. What is happening that should not be happening?

## Steps to Reproduce

1. Go to '...'
2. Click on '...'
3. Run command '...'
4. See error

## Expected Behaviour

What did you expect to happen?

## Actual Behaviour

What actually happened? Include any error messages exactly as they appeared.

## Logs

Paste relevant log output here (remove any sensitive information first).

```
# From the dashboard: Settings → Log Viewer
# From the server: journalctl -u conduit-cc -n 50 --no-pager
```

## Environment

- CCC Version: (from `GET /api/health` or the dashboard footer)
- OS and architecture: (e.g. Ubuntu 22.04 ARM64)
- Python version: (`python3 --version`)
- Deployment type: (Cloudflare proxy / Let's Encrypt / Other)
- Hardware: (e.g. Raspberry Pi 4 4GB)

## Additional Context

Add any other context, screenshots, or configuration details here.
