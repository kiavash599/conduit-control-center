---
title: Chapter 6 — Installing Conduit Control Center
category: user-guide
language: en
version: v0.3
audience: operator
---

# Chapter 6 — Installing Conduit Control Center

## Purpose of this chapter

In the previous chapters we prepared the Raspberry Pi, installed Ubuntu, learned the networking concepts, and prepared the domain and Cloudflare. We are now ready to install Conduit Control Center.

By the end of this chapter:

✓ CCC is installed.

✓ Conduit is installed.

✓ The Dashboard is available.

✓ DDNS is enabled.

✓ The services are running.

## 6.1 Before starting

**Purpose**

Making sure all prerequisites are ready.

**Checklist**

All of the following must be ready:

**Raspberry Pi**

✓ Ubuntu 22.04 LTS

✓ ARM64 (aarch64)

✓ SSH access

**Cloudflare**

✓ Active domain

✓ Active Zone

✓ Subdomain created

✓ Proxy Status = Proxied

**API Token**

✓ Created

✓ Permissions:

Zone → Zone → Read

Zone → DNS → Edit

**TLS**

✓ Cloudflare Origin Certificate

✓ Cloudflare Origin Private Key

**Conduit**

✓ Conduit v2.0.0

or the ability to download it from GitHub

**Warning**

⚠️

The installer supports only Ubuntu 22.04 ARM64. On other operating systems the installation will stop.

## 6.2 Overview of the installation process

**Purpose**

A general understanding of the installation steps.

CCC installation is done in three phases:

Phase 1

Validation

Phase 2

Installation

Phase 3

Finalization

**Phase 1**

Checking:

- The operating system
- Cloudflare
- TLS
- Admin Account
- Conduit Binary

**Phase 2**

Installing:

- CCC
- Conduit
- Nginx
- Systemd Services
- DDNS
- Firewall Rules

**Phase 3**

Starting:

- The services
- Health Checks
- Summary

## 6.3 Required information

**Purpose**

Getting familiar with the information the installer requests.

During installation, the following will be requested:

CF_API_TOKEN

CF_ZONE_NAME

CF_RECORD_NAME

TLS Certificate Path

TLS Key Path

Admin Username

Admin Password

**Example**

CF_ZONE_NAME

example.com

CF_RECORD_NAME

conduit.example.com

## 6.4 Preparing the TLS files

**Purpose**

Preparing the required certificate.

By default, CCC uses:

Cloudflare Origin Certificate

**Required files**

Example:

/home/ubuntu/origin.pem

/home/ubuntu/origin.key

**Installer validation**

The installer checks that:

✓ The Certificate exists

✓ The Key exists

✓ The Key is valid

✓ The Key and Certificate match each other

✓ The Certificate issuer is Cloudflare

## 6.5 Getting the CCC repository

**Purpose**

Obtaining the project source.

Example:

git clone https://github.com/kiavash599/conduit-control-center.git

Entering the folder:

cd conduit-control-center

**Important note**

The installer must be run from inside the repository root.

## 6.6 Running the installer

**Purpose**

Starting the installation process.

Run as Root:

sudo ./install.sh

**Warning**

The installer must be run with Root access.

## 6.7 Installer questions

**API Token**

Example:

Cloudflare API Token

**Zone Name**

Example:

example.com

**Record Name**

Example:

conduit.example.com

**TLS Certificate**

Example:

/home/ubuntu/origin.pem

**TLS Key**

Example:

/home/ubuntu/origin.key

**Admin Username**

Example:

admin

**Admin Password**

Minimum:

12 Characters

## 6.8 What does the installer actually do?

**Purpose**

Understanding the changes made to the system.

**System users**

Two users are created:

conduit-cc

conduit

Both are a:

System User

No Login Shell

**Configuration files**

Created:

/etc/conduit-cc/.env

/etc/conduit-cc/config.json

**Certificates**

Copied:

/etc/conduit-cc/tls/

**Services**

Installed:

conduit.service

conduit-cc.service

**Nginx**

Configured.

**DDNS**

Installed and scheduled.

**Firewall**

The following rules are opened:

22/tcp

80/tcp

443/tcp

## 6.9 First startup

**Purpose**

Making sure the services run correctly.

The installer runs conduit.service, then conduit-cc.service, and then checks the health status.

**Health Check**

Installation is considered successful when:

/api/health

returns a successful response.

## 6.10 Post-installation actions

**Very important warning**

⚠️

A successful installation does not mean Conduit is ready to receive traffic.

**Reason**

The UDP ports for Conduit are not opened automatically.

**You must do**

After installation, find the ports Conduit uses.

Example:

ss -ulnp | grep conduit

Then open them in the Firewall.

Example:

sudo ufw allow 12345/udp

**Why?**

Because UFW only opens:

22/tcp

80/tcp

443/tcp

## 6.11 Validation

**CCC status**

systemctl status conduit-cc

**Conduit status**

systemctl status conduit

**Health check**

curl http://127.0.0.1:8000/api/health

It should display output similar to:

{

"status": "ok"

}

**DDNS check**

tail -n 20 /var/log/conduit-cc/ddns.log

**Dashboard check**

Browser:

https://conduit.example.com

## 6.12 Troubleshooting

**The installation stops**

Check that:

✓ It is Ubuntu 22.04 ARM64.

✓ You are Root.

**Cloudflare error**

Check that:

✓ The Domain is active.

✓ The Subdomain exists.

✓ The Proxy is on.

**TLS error**

Check that:

✓ The files exist.

✓ The Certificate and Key match each other.

**The service does not run**

Check:

journalctl -u conduit-cc -n 100

or:

journalctl -u conduit -n 100

**Forgotten admin password**

The following tool is installed on the system:

sudo ccc-unlock

**Conclusion of this chapter**

Now:

✓ CCC is installed.

✓ Conduit is installed.

✓ The Dashboard is available.

✓ DDNS is enabled.

✓ The services are running.

✓ You are ready to enter the Dashboard.

**Next chapter**

In the next chapter we will enter the Dashboard for the first time and review:

- Login
- Navigation
- Dashboard Overview
- Contribution Advisor
- System Information
