---
title: فصل ۶ — نصب Conduit Control Center
category: user-guide
language: fa
version: v0.3
audience: operator
---

# فصل ۶ — نصب Conduit Control Center

## هدف این فصل

در فصل‌های قبل:

- Raspberry Pi را آماده کردیم.
- Ubuntu را نصب کردیم.
- مفاهیم شبکه را یاد گرفتیم.
- دامنه و Cloudflare را آماده کردیم.

اکنون آماده نصب Conduit Control Center هستیم.

در پایان این فصل:

✓ CCC نصب شده است.

✓ Conduit نصب شده است.

✓ Dashboard در دسترس است.

✓ DDNS فعال است.

✓ سرویس‌ها در حال اجرا هستند.

## 6.1 قبل از شروع

**هدف**

اطمینان از آماده بودن تمام پیش‌نیازها.

**چک‌لیست**

باید تمام موارد زیر آماده باشند:

**Raspberry Pi**

✓ Ubuntu 22.04 LTS

✓ ARM64 (aarch64)

✓ دسترسی SSH

**Cloudflare**

✓ دامنه فعال

✓ Zone فعال

✓ Subdomain ایجاد شده

✓ Proxy Status = Proxied

**API Token**

✓ ساخته شده

✓ دسترسی:

Zone → Zone → Read

Zone → DNS → Edit

**TLS**

✓ Cloudflare Origin Certificate

✓ Cloudflare Origin Private Key

**Conduit**

✓ Conduit v2.0.0

یا امکان دانلود آن از GitHub

**هشدار**

⚠️

نصب‌کننده فقط Ubuntu 22.04 ARM64 را پشتیبانی می‌کند.

در سایر سیستم‌عامل‌ها نصب متوقف خواهد شد.

## 6.2 نمای کلی فرآیند نصب

**هدف**

درک کلی مراحل نصب.

نصب CCC در سه فاز انجام می‌شود:

Phase 1

Validation

Phase 2

Installation

Phase 3

Finalization

**Phase 1**

بررسی:

- سیستم عامل
- Cloudflare
- TLS
- Admin Account
- Conduit Binary

**Phase 2**

نصب:

- CCC
- Conduit
- Nginx
- Systemd Services
- DDNS
- Firewall Rules

**Phase 3**

راه‌اندازی:

- سرویس‌ها
- Health Checks
- Summary

## 6.3 اطلاعات مورد نیاز

**هدف**

آشنایی با اطلاعاتی که نصب‌کننده درخواست می‌کند.

در طول نصب موارد زیر درخواست خواهند شد:

CF_API_TOKEN

CF_ZONE_NAME

CF_RECORD_NAME

TLS Certificate Path

TLS Key Path

Admin Username

Admin Password

**مثال**

CF_ZONE_NAME

example.com

CF_RECORD_NAME

conduit.example.com

## 6.4 آماده‌سازی فایل‌های TLS

**هدف**

آماده‌سازی گواهی مورد نیاز.

CCC به صورت پیش‌فرض از Cloudflare Origin Certificate استفاده می‌کند.

**فایل‌های مورد نیاز**

مثال:

/home/ubuntu/origin.pem

/home/ubuntu/origin.key

**اعتبارسنجی نصب‌کننده**

نصب‌کننده بررسی می‌کند:

✓ Certificate وجود داشته باشد

✓ Key وجود داشته باشد

✓ Key معتبر باشد

✓ Key و Certificate با هم تطابق داشته باشند

✓ صادرکننده Certificate برابر Cloudflare باشد

**نحوهٔ تهیهٔ این فایل‌ها**

اگر هنوز origin.pem و origin.key را ندارید، ابتدا گواهی Origin Cloudflare را
بسازید — به فصل ۵، بخش 5.15 (ایجاد گواهی Origin در Cloudflare) مراجعه کنید.
راهنمای کامل و گام‌به‌گام در فایل docs/tls-setup.md (مسیر A) قرار دارد. این راهنمای
تفصیلی فقط به زبان انگلیسی نگهداری می‌شود.

## 6.5 دریافت مخزن CCC

**هدف**

دریافت سورس پروژه.

نمونه:

git clone https://github.com/kiavash599/conduit-control-center.git

ورود به پوشه:

cd conduit-control-center

**نکته مهم**

نصب‌کننده باید از داخل ریشه مخزن اجرا شود.

## 6.6 اجرای نصب‌کننده

**هدف**

شروع فرآیند نصب.

اجرا به صورت Root:

sudo ./install.sh

**هشدار**

نصب‌کننده باید با دسترسی Root اجرا شود.

## 6.7 سؤالات نصب‌کننده

**API Token**

مثال:

Cloudflare API Token

**Zone Name**

مثال:

example.com

**Record Name**

مثال:

conduit.example.com

**TLS Certificate**

مثال:

/home/ubuntu/origin.pem

**TLS Key**

مثال:

/home/ubuntu/origin.key

**Admin Username**

مثال:

admin

**Admin Password**

حداقل:

12 Characters

## 6.8 نصب‌کننده واقعاً چه کار می‌کند؟

**هدف**

درک تغییراتی که روی سیستم انجام می‌شوند.

**کاربران سیستمی**

دو کاربر ایجاد می‌شوند:

conduit-cc

conduit

هر دو:

System User

No Login Shell

هستند.

**فایل‌های پیکربندی**

ایجاد می‌شوند:

/etc/conduit-cc/.env

/etc/conduit-cc/config.json

**گواهی‌ها**

کپی می‌شوند:

/etc/conduit-cc/tls/

**سرویس‌ها**

نصب می‌شوند:

conduit.service

conduit-cc.service

**Nginx**

پیکربندی می‌شود.

**DDNS**

نصب و زمان‌بندی می‌شود.

**Firewall**

قوانین زیر باز می‌شوند:

22/tcp

80/tcp

443/tcp

## 6.9 اولین راه‌اندازی

**هدف**

اطمینان از اجرای صحیح سرویس‌ها.

نصب‌کننده:

conduit.service

را اجرا می‌کند.

سپس:

conduit-cc.service

را اجرا می‌کند.

سپس وضعیت سلامت را بررسی می‌کند.

**Health Check**

نصب زمانی موفق محسوب می‌شود که:

/api/health

پاسخ موفق بازگرداند.

## 6.10 اقدامات پس از نصب

**هشدار بسیار مهم**

⚠️

نصب موفق به این معنا نیست که Conduit آماده دریافت ترافیک است.

**دلیل**

پورت‌های UDP مربوط به Conduit به صورت خودکار باز نمی‌شوند.

**باید انجام دهید**

پس از نصب:

پورت‌های مورد استفاده Conduit را پیدا کنید.

مثال:

ss -ulnp | grep conduit

سپس در Firewall باز کنید.

مثال:

sudo ufw allow 12345/udp

**چرا؟**

زیرا UFW فقط:

22/tcp

80/tcp

443/tcp

را باز می‌کند.

## 6.11 اعتبارسنجی

**وضعیت CCC**

systemctl status conduit-cc

**وضعیت Conduit**

systemctl status conduit

**بررسی Health**

curl http://127.0.0.1:8000/api/health

باید خروجی مشابه:

{

"status": "ok"

}

نمایش دهد.

**بررسی DDNS**

tail -n 20 /var/log/conduit-cc/ddns.log

**بررسی Dashboard**

مرورگر:

https://conduit.example.com

## 6.12 عیب‌یابی

**نصب متوقف می‌شود**

بررسی کنید:

✓ Ubuntu 22.04 ARM64 باشد.

✓ Root باشید.

**خطای Cloudflare**

بررسی کنید:

✓ Domain فعال باشد.

✓ Subdomain وجود داشته باشد.

✓ Proxy روشن باشد.

**خطای TLS**

بررسی کنید:

✓ فایل‌ها وجود داشته باشند.

✓ Certificate و Key با هم تطابق داشته باشند.

**سرویس اجرا نمی‌شود**

بررسی:

journalctl -u conduit-cc -n 100

یا:

journalctl -u conduit -n 100

**فراموشی رمز مدیر**

ابزار زیر در سیستم نصب می‌شود:

sudo ccc-unlock

**نتیجه این فصل**

اکنون:

✓ CCC نصب شده است.

✓ Conduit نصب شده است.

✓ Dashboard در دسترس است.

✓ DDNS فعال است.

✓ سرویس‌ها اجرا می‌شوند.

✓ آماده ورود به Dashboard هستید.

**فصل بعد**

در فصل بعد برای اولین بار وارد Dashboard خواهیم شد و:

- Login
- Navigation
- Dashboard Overview
- Contribution Advisor
- System Information

را بررسی خواهیم کرد.
