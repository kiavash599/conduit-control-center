---
title: Chapter 13 — System Maintenance and Troubleshooting
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 13 — System Maintenance and Troubleshooting

## Purpose of this chapter

After installing and setting up CCC, the most important question is:

What do I do if a problem arises tomorrow?

This chapter helps you:

- Monitor the system's health
- Identify common problems
- Recover the services
- Check the logs
- Return the system to an operational state in critical situations

Part A — Monitoring and daily operations

## 13.1 Service architecture

CCC consists of two main services:

**Conduit**

conduit.service

Responsibility:

- Running the Conduit node
- Providing the proxy service
- Generating Metrics

**CCC**

conduit-cc.service

Responsibility:

- Dashboard
- API
- Configuration Management
- Backup & Restore
- Personal Mode
- Ryve

**Important note**

Both services have:

Restart=on-failure

So many temporary errors are recovered automatically.

## 13.2 Checking service status

**Through the Dashboard**

The Dashboard displays the node status.

**Through SSH**

Checking Conduit:

sudo systemctl status conduit

Checking CCC:

sudo systemctl status conduit-cc

## 13.3 The difference between Health and Status

**Very important note**

Many users confuse these two.

**Health**

/api/health

only shows:

CCC Running

**Status**

/api/status

shows:

- Conduit status
- Broker status
- Uptime

**Result**

A successful response from:

/api/health

does not necessarily mean Conduit is healthy.

## 13.4 Daily health check

It is recommended to check the following once a day:

**Node Status**

should be:

Running

**Broker State**

should be:

Live

**DDNS Status**

should not be:

Error

**Advisor**

should not have serious Warnings.

## 13.5 Logs

**Conduit log**

journalctl -u conduit

**CCC log**

journalctl -u conduit-cc

**Viewing the last 50 lines**

journalctl -u conduit -n 50

journalctl -u conduit-cc -n 50

## 13.6 DDNS log

File:

/var/log/conduit-cc/ddns.log

**Important warning**

In version v0.3.0:

Automatic Log Rotation
=
Not Implemented

So you must keep an eye on the file's growth.

**Part B — Troubleshooting and recovery**

## 13.7 The Dashboard does not open

**Step 1**

Check the CCC service:

sudo systemctl status conduit-cc

**Step 2**

Check Health:

curl http://127.0.0.1:8000/api/health

**Expected result**

{{

"status": "ok",

"version": "<APP_VERSION>"

}

For example:
{

"status": "ok",

"version": "0.3.0"

}
This Endpoint only shows the health of CCC; it does not show the health of Conduit.

**Step 3**

Check the logs:

journalctl -u conduit-cc -n 100

## 13.8 Conduit has stopped

**Check the status**

sudo systemctl status conduit

**Restart**

sudo systemctl restart conduit

**Check again**

sudo systemctl status conduit

## 13.9 Broker Disconnected

If the Dashboard shows:

Broker Disconnected

**First**

Check Conduit.

**Then**

Check the Advisor.

**Then**

Check the Conduit log.

journalctl -u conduit -n 100

## 13.10 DDNS does not work

**Check the status**

Dashboard → DDNS Status

**Check the log**

tail -n 20 /var/log/conduit-cc/ddns.log

**Run manually**

sudo -u conduit-cc /usr/local/bin/cloudflare-ddns.sh

**Check Cron**

crontab -u conduit-cc -l

## 13.11 Personal Mode does not become active

**Common cause**

The Identity has not been created.

**Check the status**

Dashboard → Personal Mode

**Valid state**

Active

**Inactive state**

Created – Inactive

In this state:

Max Personal Clients > 0

has not yet been set.

## 13.12 The Ryve QR is not generated

**Common causes**

- The Helper is not installed
- No sudo permission exists
- Conduit is not available

**Action**

Check the Conduit status:

sudo systemctl status conduit

**Then**

The CCC log:

journalctl -u conduit-cc -n 100

## 13.13 The Restore failed

**Check the status**

Dashboard → Backup & Restore

The status may be:

Rolled Back

This means the Restore failed but the system returned to its previous state.

## 13.14 Rollback Failed

This is the most serious visible state.

Meaning:

Restore Failed
+
Rollback Failed

In this case:

manual operator intervention is required.

## 13.15 Admin account lockout

If the wrong password is entered several times, the account is temporarily locked.

**Recovery**

From SSH:

sudo ccc-unlock <username>

Example:

sudo ccc-unlock admin

## 13.16 Safe Recovery Procedure

If you are not sure where the problem is, run the following steps.

**Step 1**

Check CCC:

sudo systemctl status conduit-cc

**Step 2**

Check Conduit:

sudo systemctl status conduit

**Step 3**

Check Health:

curl http://127.0.0.1:8000/api/health

**Step 4**

Check Status:

Dashboard → Node Status

**Step 5**

Check the logs:

journalctl -u conduit -n 100
journalctl -u conduit-cc -n 100

**Step 6**

If needed:

sudo systemctl restart conduit
sudo systemctl restart conduit-cc

## 13.17 Safe update

The recommended method:

sudo bash update.sh

**Important note**

update.sh does not just perform an Update. This process performs:

Backup
↓
Upgrade
↓
Health Verify
↓
Rollback On Failure

## 13.18 When should I perform a Restore?

Restore is the last resort.

Before Restore:

- Check the logs
- Restart the services
- Check the settings

Restore is appropriate when:

- The system is severely broken
- The settings have been lost
- A migration to new hardware is being done

## 13.19 When should I perform a Reinstall?

In most cases:

Reinstall
=
Not Required

First try:

- Restart
- Repair
- Restore

## 13.20 Conclusion of this chapter

Now you know:

✓ How the services work

✓ What the difference between Health and Status is

✓ Where the logs are located

✓ How to troubleshoot DDNS

✓ How to troubleshoot Personal Mode

✓ How to troubleshoot Ryve

✓ How to check a Restore

✓ How to resolve a Lockout

✓ How to perform safe Recovery

### One-Click Update and rollback

Day-to-day upgrades use dashboard One-Click Update; manual `update.sh` over SSH is retained for initial install, recovery, and emergency maintenance. If an update auto-rolls-back, your node keeps the previous version — diagnose via the worker log (`/var/lib/conduit-cc/update-worker.log`) and the status file (`/var/lib/conduit-cc/update-status.json`), then retry. Full flow: **[Software Updates & Signed Releases](software-updates-and-signed-releases.md)**.

**Next chapter**

In Chapter 14 we will examine:

Security Model

and fully explain CCC's security architecture, Secret management, Least Privilege, Backup Encryption, Personal Mode Security, and Ryve Security.
