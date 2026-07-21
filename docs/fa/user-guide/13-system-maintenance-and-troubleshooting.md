---
title: فصل ۱۳ — نگهداری سیستم و عیب‌یابی
category: user-guide
language: fa
version: v0.3
audience: operator
---

# فصل ۱۳ — نگهداری سیستم و عیب‌یابی

## هدف این فصل

پس از نصب و راه‌اندازی CCC، مهم‌ترین سؤال این است:

اگر فردا مشکلی پیش آمد چه کار کنم؟

این فصل به شما کمک می‌کند:

- سلامت سیستم را پایش کنید
- مشکلات رایج را تشخیص دهید
- سرویس‌ها را بازیابی کنید
- لاگ‌ها را بررسی کنید
- در شرایط بحرانی سیستم را به وضعیت عملیاتی بازگردانید

بخش A — پایش و عملیات روزمره

## 13.1 معماری سرویس‌ها

CCC از دو سرویس اصلی تشکیل شده است:

**Conduit**

conduit.service

وظیفه:

- اجرای نود Conduit
- ارائه سرویس پروکسی
- تولید Metrics

**CCC**

conduit-cc.service

وظیفه:

- Dashboard
- API
- Configuration Management
- Backup & Restore
- Personal Mode
- Ryve

**نکته مهم**

هر دو سرویس:

Restart=on-failure

دارند.

بنابراین بسیاری از خطاهای موقت به صورت خودکار بازیابی می‌شوند.

## 13.2 بررسی وضعیت سرویس‌ها

**از طریق Dashboard**

Dashboard وضعیت نود را نمایش می‌دهد.

**از طریق SSH**

بررسی Conduit:

sudo systemctl status conduit

بررسی CCC:

sudo systemctl status conduit-cc

## 13.3 تفاوت Health و Status

**نکته بسیار مهم**

بسیاری از کاربران این دو را با هم اشتباه می‌گیرند.

**Health**

/api/health

فقط نشان می‌دهد:

CCC Running

**Status**

/api/status

نشان می‌دهد:

- وضعیت Conduit
- وضعیت Broker
- Uptime

**نتیجه**

اگر:

/api/health

موفق باشد:

الزاماً به معنی سالم بودن Conduit نیست.

## 13.4 بررسی سلامت روزانه

توصیه می‌شود روزی یک بار موارد زیر را بررسی کنید:

**Node Status**

باید:

Running

باشد.

**Broker State**

باید:

Live

باشد.

**DDNS Status**

نباید:

Error

باشد.

**Advisor**

نباید Warningهای جدی داشته باشد.

## 13.5 لاگ‌ها

**لاگ Conduit**

journalctl -u conduit

**لاگ CCC**

journalctl -u conduit-cc

**مشاهده 50 خط آخر**

journalctl -u conduit -n 50

journalctl -u conduit-cc -n 50

## 13.6 لاگ DDNS

فایل:

/var/log/conduit-cc/ddns.log

**هشدار مهم**

در نسخه v0.3.0:

Automatic Log Rotation
=
Not Implemented

بنابراین باید رشد فایل را زیر نظر داشته باشید.

**بخش B — عیب‌یابی و بازیابی**

## 13.7 Dashboard باز نمی‌شود

**مرحله 1**

بررسی سرویس CCC:

sudo systemctl status conduit-cc

**مرحله 2**

بررسی Health:

curl http://127.0.0.1:8000/api/health

**نتیجه مورد انتظار**

{{

"status": "ok",

"version": "<APP_VERSION>"

}

بطور مثال:
{

"status": "ok",

"version": "0.3.0"

}
این Endpoint فقط سلامت CCC را نشان می‌دهد.

سلامت Conduit را نشان نمی‌دهد.

**مرحله 3**

بررسی لاگ‌ها:

journalctl -u conduit-cc -n 100

## 13.8 Conduit متوقف شده است

**بررسی وضعیت**

sudo systemctl status conduit

**راه‌اندازی مجدد**

sudo systemctl restart conduit

**بررسی مجدد**

sudo systemctl status conduit

## 13.9 Broker Disconnected

اگر Dashboard نشان دهد:

Broker Disconnected

**ابتدا**

Conduit را بررسی کنید.

**سپس**

Advisor را بررسی کنید.

**سپس**

لاگ Conduit را بررسی کنید.

journalctl -u conduit -n 100

## 13.10 DDNS کار نمی‌کند

**بررسی وضعیت**

Dashboard → DDNS Status

**بررسی لاگ**

tail -n 20 /var/log/conduit-cc/ddns.log

**اجرای دستی**

sudo -u conduit-cc /usr/local/bin/cloudflare-ddns.sh

**بررسی Cron**

crontab -u conduit-cc -l

## 13.11 Personal Mode فعال نمی‌شود

**علت رایج**

Identity ساخته نشده است.

**بررسی وضعیت**

Dashboard → Personal Mode

**وضعیت معتبر**

Active

**وضعیت غیرفعال**

Created – Inactive

در این حالت:

Max Personal Clients > 0

تنظیم نشده است.

## 13.12 Ryve QR تولید نمی‌شود

**علت‌های رایج**

- Helper نصب نشده
- مجوز sudo وجود ندارد
- Conduit در دسترس نیست

**اقدام**

بررسی وضعیت Conduit:

sudo systemctl status conduit

**سپس**

لاگ CCC:

journalctl -u conduit-cc -n 100

## 13.13 Restore شکست خورده است

**بررسی وضعیت**

Dashboard → Backup & Restore

ممکن است وضعیت:

Rolled Back

باشد.

این یعنی:

Restore ناموفق بوده اما سیستم به وضعیت قبلی بازگشته است.

## 13.14 Rollback Failed

این جدی‌ترین وضعیت قابل مشاهده است.

معنی:

Restore Failed
+
Rollback Failed

در این حالت:

مداخله دستی اپراتور لازم است.

## 13.15 قفل شدن حساب مدیر

اگر چند بار رمز اشتباه وارد شود:

حساب موقتاً قفل می‌شود.

**بازیابی**

از SSH:

sudo ccc-unlock <username>

مثال:

sudo ccc-unlock admin

## 13.16 Safe Recovery Procedure

اگر مطمئن نیستید مشکل از کجاست:

مراحل زیر را اجرا کنید.

**مرحله 1**

بررسی CCC:

sudo systemctl status conduit-cc

**مرحله 2**

بررسی Conduit:

sudo systemctl status conduit

**مرحله 3**

بررسی Health:

curl http://127.0.0.1:8000/api/health

**مرحله 4**

بررسی Status:

Dashboard → Node Status

**مرحله 5**

بررسی لاگ‌ها:

journalctl -u conduit -n 100
journalctl -u conduit-cc -n 100

**مرحله 6**

در صورت نیاز:

sudo systemctl restart conduit
sudo systemctl restart conduit-cc

## 13.17 به‌روزرسانی امن

روش توصیه‌شده:

sudo bash update.sh

**نکته مهم**

`update.sh` فقط Update انجام نمی‌دهد.

این فرآیند:

Backup
↓
Upgrade
↓
Health Verify
↓
Rollback On Failure

را انجام می‌دهد.

## 13.18 چه زمانی باید Restore انجام دهم؟

Restore آخرین راهکار است.

قبل از Restore:

- لاگ‌ها را بررسی کنید
- سرویس‌ها را Restart کنید
- تنظیمات را بررسی کنید

Restore زمانی مناسب است که:

- سیستم به شدت خراب شده باشد
- تنظیمات از بین رفته باشند
- مهاجرت به سخت‌افزار جدید انجام شود

## 13.19 چه زمانی باید Reinstall انجام دهم؟

در بیشتر موارد:

Reinstall
=
Not Required

ابتدا:

- Restart
- Repair
- Restore

را امتحان کنید.

## 13.20 نتیجه این فصل

اکنون می‌دانید:

✓ سرویس‌ها چگونه کار می‌کنند

✓ Health و Status چه تفاوتی دارند

✓ لاگ‌ها کجا قرار دارند

✓ DDNS را چگونه عیب‌یابی کنید

✓ Personal Mode را چگونه عیب‌یابی کنید

✓ Ryve را چگونه عیب‌یابی کنید

✓ Restore را چگونه بررسی کنید

✓ Lockout را چگونه رفع کنید

✓ Recovery ایمن را چگونه انجام دهید

### به‌روزرسانی تک‌کلیکی و بازگردانی

ارتقاهای روزمره از به‌روزرسانی تک‌کلیکی داشبورد استفاده می‌کنند؛ اجرای دستی `update.sh` از طریق SSH برای نصب اولیه، بازیابی و نگهداری اضطراری حفظ شده است. اگر به‌روزرسانی به‌صورت خودکار بازگردانی شود، گره شما نسخهٔ قبلی را نگه می‌دارد — با استفاده از گزارش root-only worker (`/var/lib/ccc-update/update-worker.log`) و فایل وضعیت منتشرشده (`/var/lib/ccc-status/update-status.json`) عیب‌یابی کنید و دوباره تلاش نمایید. جریان کامل: **[به‌روزرسانی نرم‌افزار و انتشارهای امضاشده](software-updates-and-signed-releases.md)**.

**فصل بعد**

در فصل 14:

Security Model

را بررسی خواهیم کرد و معماری امنیتی CCC، مدیریت Secretها، Least Privilege، Backup Encryption، Personal Mode Security و Ryve Security را به صورت کامل توضیح خواهیم داد.
