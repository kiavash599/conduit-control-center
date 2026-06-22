---
title: فصل ۱۵ — مدیریت پیشرفته (Advanced Administration)
category: user-guide
language: fa
version: v0.3
audience: operator
---

# فصل ۱۵ — مدیریت پیشرفته (Advanced Administration)

## هدف این فصل

در فصل‌های قبل یاد گرفتیم چگونه:

- CCC را نصب کنیم
- سیستم را پیکربندی کنیم
- Backup بگیریم
- Restore انجام دهیم
- مشکلات رایج را رفع کنیم

اکنون می‌خواهیم مانند یک Administrator حرفه‌ای سیستم را مدیریت کنیم.

## در پایان این فصل

✓ معماری مدیریتی CCC را خواهید شناخت.

✓ سرویس‌ها را مدیریت خواهید کرد.

✓ فرآیند Update را درک خواهید کرد.

✓ معماری تنظیمات را خواهید شناخت.

✓ کاربران را مدیریت خواهید کرد.

✓ عملیات Recovery را خواهید شناخت.

✓ محدودیت‌های عملیاتی سیستم را خواهید شناخت.

## 15.1 نمای کلی مدیریت سیستم

CCC بر پایه:

systemd

ساخته شده است.

تقریباً تمام عملیات مدیریتی حول سه محور انجام می‌شوند:

Services
Configuration
Recovery

## 15.2 سرویس‌های اصلی

**Conduit**

conduit.service

وظیفه:

- اجرای نود Conduit
- ارتباط با Broker
- تولید Metrics

**CCC**

conduit-cc.service

وظیفه:

- Dashboard
- API
- Backup
- Restore
- Advisor
- Personal Mode
- Ryve

## 15.3 مدیریت سرویس‌ها

**مشاهده وضعیت**

Conduit:

sudo systemctl status conduit

CCC:

sudo systemctl status conduit-cc

**راه‌اندازی مجدد**

Conduit:

sudo systemctl restart conduit

CCC:

sudo systemctl restart conduit-cc

**توقف سرویس**

sudo systemctl stop conduit

sudo systemctl stop conduit-cc

**شروع سرویس**

sudo systemctl start conduit

sudo systemctl start conduit-cc

## 15.4 Restart خودکار

هر دو سرویس دارای:

Restart=on-failure

هستند.

بنابراین در بسیاری از خطاهای موقت:

systemd سرویس را مجدداً اجرا می‌کند.

## 15.5 فرآیند Update

**هدف**

به‌روزرسانی ایمن سیستم.

دستور اصلی:

sudo bash update.sh

**نکته مهم**

update.sh صرفاً یک Update Script نیست.

در واقع:

Update Manager
+
Recovery Manager

است.

## 15.6 مراحل Update

فرآیند واقعی:

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

یا:

Pre-flight
↓
Backup
↓
Deploy
↓
Health Failure
↓
Rollback

## 15.7 محل Backupهای Update

Backupهای Update در:

/var/backups/conduit-cc/

نگهداری می‌شوند.

**سیاست نگهداری**

آخرین:

3

نسخه حفظ می‌شوند.

## 15.8 Rollback خودکار

اگر نسخه جدید سالم نباشد:

Automatic Rollback

اجرا می‌شود.

**هدف**

بازگرداندن آخرین نسخه سالم.

## 15.9 معماری تنظیمات

CCC دارای سه لایه تنظیمات است.

**لایه اول**

.env

شامل:

- Secretها
- Tokenها
- Password Hashها
- Portها

**لایه دوم**

config.json

شامل:

- Thresholdها
- Timeoutها
- Feature Flags
- Retention Settings

**لایه سوم**

systemd drop-in

شامل:

- Runtime Conduit Settings

## 15.10 فایل .env

مسیر:

/etc/conduit-cc/.env

**نمونه موارد**

SESSION_SECRET

ADMIN_PASSWORD_HASH

CF_API_TOKEN

## 15.11 فایل config.json

مسیر:

/etc/conduit-cc/config.json

**نمونه موارد**

Thresholds

Retention

Feature Toggles

Monitoring Settings

## 15.12 Runtime Configuration

تنظیمات اعمال‌شده Conduit در:

systemd drop-in

ذخیره می‌شوند.

این تنظیمات توسط Dashboard تغییر می‌کنند.

## 15.13 مدیریت کاربران

نسخه v0.3.0 از مدل:

Single Administrator

استفاده می‌کند.

**نکته**

Multi-user وجود ندارد.

## 15.14 تغییر رمز عبور

از طریق Dashboard:

Settings
↓
Change Password

**رفتار سیستم**

Verify Current Password
↓
Create New Hash
↓
Invalidate Sessions
↓
Save

**مزیت**

Sessionهای قبلی معتبر باقی نمی‌مانند.

## 15.15 رفع قفل حساب

در صورت Lockout:

sudo ccc-unlock <username>

مثال:

sudo ccc-unlock admin

## 15.16 پایش سیستم

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

## 15.17 Metrics داخلی Conduit

Conduit نیز Metrics اختصاصی دارد.

آدرس:

127.0.0.1:9090

## 15.18 مدیریت لاگ‌ها

**لاگ CCC**

journalctl -u conduit-cc

**لاگ Conduit**

journalctl -u conduit

**DDNS**

/var/log/conduit-cc/ddns.log

**هشدار مهم**

DDNS Log Rotation
Not Implemented

مدیریت فایل با اپراتور است.

## 15.19 عملیات Backup

Backup فقط به صورت:

Manual

پشتیبانی می‌شود.

**نکته**

Scheduler داخلی وجود ندارد.

## 15.20 عملیات Restore

Restore شامل:

Inspect
↓
Compatibility Check
↓
Restore
↓
Health Verify
↓
Rollback

است.

## 15.21 Disaster Recovery

اگر سیستم کاملاً از دست برود:

**مرحله 1**

Ubuntu نصب کنید.

**مرحله 2**

CCC را نصب کنید.

**مرحله 3**

Restore انجام دهید.

**مرحله 4**

Secretهای حذف‌شده را بازگردانید.

**مواردی که باید دوباره ایجاد شوند**

CF_API_TOKEN

Personal Identity

Ryve Claim

TLS Material

## 15.22 محدودیت‌های عملیاتی

**Single Worker Requirement**

**بسیار مهم**

⚠️

در نسخه v0.3.0:

uvicorn --workers 1

باید حفظ شود.

**دلیل**

برخی Stateها در حافظه نگهداری می‌شوند:

Advisor State

Restore State

Ryve State

**نتیجه**

تغییر تعداد Workerها پشتیبانی نمی‌شود.

## 15.23 محدوده تنظیمات

**Max Common Clients**

1 – 1000

**Personal Clients**

0 – 1000

**Bandwidth**

1 – 1000 Mbps

یا:

Unlimited (-1)

## 15.24 بهترین شیوه‌های مدیریتی

**توصیه 1**

قبل از هر Update:

Backup بگیرید.

**توصیه 2**

بعد از هر Update:

Dashboard را بررسی کنید.

**توصیه 3**

فایل:

/var/log/conduit-cc/ddns.log

را مانیتور کنید.

**توصیه 4**

تنظیمات Runtime را دستی ویرایش نکنید.

**توصیه 5**

تعداد Workerها را تغییر ندهید.

## 15.25 نتیجه این فصل

اکنون می‌دانید:

✓ سرویس‌ها چگونه مدیریت می‌شوند

✓ Update چگونه کار می‌کند

✓ Rollback چگونه انجام می‌شود

✓ تنظیمات چگونه سازماندهی شده‌اند

✓ کاربران چگونه مدیریت می‌شوند

✓ Monitoring چگونه انجام می‌شود

✓ Recovery چگونه انجام می‌شود

✓ محدودیت‌های عملیاتی سیستم چیست

**فصل بعد**

در فصل 16:

Frequently Asked Questions (FAQ)

را بررسی خواهیم کرد و متداول‌ترین پرسش‌های اپراتورهای CCC را پاسخ خواهیم داد.
