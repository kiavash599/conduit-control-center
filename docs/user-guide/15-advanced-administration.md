---
title: Chapter 15 — Advanced Administration
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 15 — Advanced Administration

## Purpose of this chapter

In the previous chapters we learned how to:

- Install CCC
- Configure the system
- Take a Backup
- Perform a Restore
- Resolve common problems

Now we want to manage the system like a professional Administrator.

## By the end of this chapter

✓ You will know CCC's administrative architecture.

✓ You will manage the services.

✓ You will understand the Update process.

✓ You will know the configuration architecture.

✓ You will manage users.

✓ You will know Recovery operations.

✓ You will know the system's operational limits.

## 15.1 System management overview

CCC is built on:

systemd

Almost all administrative operations are done around three axes:

Services
Configuration
Recovery

## 15.2 The main services

**Conduit**

conduit.service

Responsibility:

- Running the Conduit node
- Communicating with the Broker
- Generating Metrics

**CCC**

conduit-cc.service

Responsibility:

- Dashboard
- API
- Backup
- Restore
- Advisor
- Personal Mode
- Ryve

## 15.3 Managing the services

**Viewing the status**

Conduit:

sudo systemctl status conduit

CCC:

sudo systemctl status conduit-cc

**Restarting**

Conduit:

sudo systemctl restart conduit

CCC:

sudo systemctl restart conduit-cc

**Stopping a service**

sudo systemctl stop conduit

sudo systemctl stop conduit-cc

**Starting a service**

sudo systemctl start conduit

sudo systemctl start conduit-cc

## 15.4 Automatic Restart

Both services have:

Restart=on-failure

So for many temporary errors, systemd runs the service again.

## 15.5 The Update process

**Purpose**

Safely updating the system.

The main command:

sudo bash update.sh

**Important note**

update.sh is not merely an Update Script. It is, in fact, both an:

Update Manager
+
Recovery Manager

## 15.6 Update steps

The actual process:

Pre-flight
↓
Backup
↓
Install Dependencies
↓
Deploy
↓
Health Verify
↓
Success

or:

Pre-flight
↓
Backup
↓
Deploy
↓
Health Failure
↓
Rollback

## 15.7 Location of Update backups

Update backups are kept in:

/var/backups/conduit-cc/

**Retention policy**

The last:

3

versions are kept.

## 15.8 Automatic Rollback

If the new version is not healthy, an:

Automatic Rollback

is executed.

**Purpose**

Restoring the last healthy version.

## 15.9 Configuration architecture

CCC has three configuration layers.

**First layer**

.env

including:

- Secrets
- Tokens
- Password Hashes
- Ports

**Second layer**

config.json

including:

- Thresholds
- Timeouts
- Feature Flags
- Retention Settings

**Third layer**

systemd drop-in

including:

- Runtime Conduit Settings

## 15.10 The .env file

Path:

/etc/conduit-cc/.env

**Sample items**

SESSION_SECRET

ADMIN_PASSWORD_HASH

CF_API_TOKEN

## 15.11 The config.json file

Path:

/etc/conduit-cc/config.json

**Sample items**

Thresholds

Retention

Feature Toggles

Monitoring Settings

## 15.12 Runtime Configuration

The applied Conduit settings are stored in a:

systemd drop-in

and are changed through the Dashboard.

## 15.13 User management

Version v0.3.0 uses the model:

Single Administrator

**Note**

There is no Multi-user.

## 15.14 Changing the password

Through the Dashboard:

Settings
↓
Change Password

**System behavior**

Verify Current Password
↓
Create New Hash
↓
Invalidate Sessions
↓
Save

**Benefit**

Previous Sessions do not remain valid.

## 15.15 Unlocking the account

In case of a Lockout:

sudo ccc-unlock <username>

Example:

sudo ccc-unlock admin

## 15.16 System monitoring

**Health**

/api/health

**Status**

/api/status

**System Metrics**

/api/metrics/system

**DDNS**

/api/ddns/status

**Advisor**

/api/advisor

## 15.17 Conduit's internal Metrics

Conduit also has dedicated Metrics.

Address:

127.0.0.1:9090

## 15.18 Log management

**CCC log**

journalctl -u conduit-cc

**Conduit log**

journalctl -u conduit

**DDNS**

/var/log/conduit-cc/ddns.log

**Important warning**

DDNS Log Rotation
Not Implemented

File management is the operator's responsibility.

## 15.19 Backup operations

Backup is supported only as:

Manual

**Note**

There is no built-in Scheduler.

## 15.20 Restore operations

Restore includes:

Inspect
↓
Compatibility Check
↓
Restore
↓
Health Verify
↓
Rollback

## 15.21 Disaster Recovery

If the system is completely lost, follow these steps.

**Step 1**

Install Ubuntu.

**Step 2**

Install CCC.

**Step 3**

Perform a Restore.

**Step 4**

Restore the excluded Secrets.

**Items that must be re-created**

CF_API_TOKEN

Personal Identity

Ryve Claim

TLS Material

## 15.22 Operational limits

**Single Worker Requirement**

**Very important**

⚠️

In version v0.3.0:

uvicorn --workers 1

must be kept.

**Reason**

Some State is kept in memory, including:

Advisor State

Restore State

Ryve State

**Result**

Changing the number of Workers is not supported.

## 15.23 Configuration ranges

**Max Common Clients**

1 – 1000

**Personal Clients**

0 – 1000

**Bandwidth**

1 – 1000 Mbps

or:

Unlimited (-1)

## 15.24 Administrative best practices

**Recommendation 1**

Before every Update:

take a Backup.

**Recommendation 2**

After every Update:

check the Dashboard.

**Recommendation 3**

Monitor the file:

/var/log/conduit-cc/ddns.log

**Recommendation 4**

Do not manually edit the Runtime settings.

**Recommendation 5**

Do not change the number of Workers.

## 15.25 Conclusion of this chapter

Now you know:

✓ How the services are managed

✓ How Update works

✓ How Rollback is done

✓ How the settings are organized

✓ How users are managed

✓ How Monitoring is done

✓ How Recovery is done

✓ What the system's operational limits are

**Next chapter**

In Chapter 16 we will examine:

Frequently Asked Questions (FAQ)

and answer the most common questions of CCC operators.
