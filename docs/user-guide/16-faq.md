---
title: Chapter 16 — Frequently Asked Questions (FAQ)
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 16 — Frequently Asked Questions (FAQ)

## Purpose of this chapter

This chapter provides short, direct answers to the most common questions of CCC operators.

**Installation and hardware**

**Is Raspberry Pi 4 required?**

No.

CCC is tested and recommended on Raspberry Pi 4, but it is not limited to it.

**Can Raspberry Pi 3 be used?**

Yes.

But it is recommended to check its performance before practical use.

**Is 2 GB RAM enough?**

Yes.

CCC has been successfully run on a Raspberry Pi 4 with 2 GB RAM.

**Is an SSD required?**

No.

But an SSD usually provides:

- Greater stability
- Better speed
- Longer lifespan

**Can Wi-Fi be used?**

Yes.

But for a permanent node:

Ethernet

is recommended.

**Networking and Cloudflare**

**Why do I need Cloudflare?**

Cloudflare performs three important tasks:

- DNS
- TLS Termination
- Protecting the server's real IP

**Why must the Proxy be on?**

In v0.3.0 the installer checks that:

Orange Cloud

is enabled.

**Is a static IP required?**

No.

DDNS was designed for exactly this.

**Does DDNS work with a dynamic IP?**

Yes.

The DDNS script checks the public IP every 5 minutes.

**Does DDNS support IPv6?**

No.

In v0.3.0 only:

A Record / IPv4

is supported.

**Dashboard and operations**

**The Dashboard does not open. What should I do?**

First check:

sudo systemctl status conduit-cc

**What is the difference between Health and Status?**

Health:

/api/health

only shows that CCC is running.

Status:

/api/status

shows the real status of the node and the Broker.

**Why is Broker Disconnected displayed?**

Common reasons:

- A Conduit problem
- A problem communicating with the Broker
- The Conduit service has stopped

**Contribution Advisor**

**Does the Advisor apply settings automatically?**

No.

The Advisor only makes suggestions.

**Does the Advisor track users?**

No.

CCC uses only Aggregate data.

**Why does the Advisor not provide a suggestion yet?**

It may be that:

- There is not enough history
- The Traffic Collector is not enabled
- The 7-day data has not been collected yet

**Personal Mode**

**Does Personal Mode become active by creating an Identity?**

No.

An Identity alone is not enough.

You must set:

Max Personal Clients > 0

**Does the personal QR expire?**

No.

It is valid as long as the Identity exists.

**Can the Identity be deleted?**

No.

In v0.3.0 there is no direct deletion.

**Is Personal Mode saved in a Backup?**

The settings are saved, but the actual Identity is not.

**Ryve**

**What is Ryve?**

CCC only generates the QR needed for the Claim; the actual Claim is done by the Ryve app.

**Is the Ryve QR sensitive?**

Yes.

It must be treated like a private Credential.

**Is Ryve saved in a Backup?**

No.

**Does generating the QR cause a Restart?**

No.

**Backup & Restore**

**What does a Backup include?**

In short:

- Database
- Configurations
- Settings

**What does a Backup not include?**

The following are not included:

CF_API_TOKEN
SESSION_SECRET
TLS Private Key
Conduit Private Key
Ryve Identity

**What happens if I forget the Passphrase?**

The Backup will be unrecoverable.

**Is a Restore done instantly?**

No.

A Restore runs in the Background.

**What happens if a Restore fails?**

CCC tries to perform:

Automatic Rollback

**Security**

**Why is there only one Administrator?**

Version v0.3.0 uses the model:

Single Administrator

**Is the password stored as Plain Text?**

No.

A Hash is stored.

**Why is CSRF used?**

To prevent forged requests from other sites.

**Why are Secrets not stored in a Backup?**

To prevent the transfer or disclosure of sensitive information.

**Advanced administration**

**Can the number of Workers be increased?**

No.

In v0.3.0:

workers = 1

must be kept.

**Is there automatic Backup?**

No.

Backup is done only manually.

**Does the DDNS log have Rotation?**

No.

In v0.3.0:

/var/log/conduit-cc/ddns.log

is not rotated automatically.

**Can the Drop-in file be edited manually?**

It is possible, but not recommended; use the Dashboard instead.

**I have forgotten the password. What should I do?**

If you have SSH access:

sudo ccc-unlock <username>

is used to resolve a Lockout.

**What is the best recommendation for operators?**

1. Perform regular updates.
2. Take a Backup before an Update.
3. Keep the Passphrase secure.
4. Treat the Personal and Ryve QRs as confidential.
5. Check the Dashboard periodically.
6. Monitor the DDNS log.

**Chapter conclusion**

You now know the answers to the most common questions related to:

- Installation
- Networking
- Dashboard
- Advisor
- Personal Mode
- Ryve
- Backup
- Security
- Administration
