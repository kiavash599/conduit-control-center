---
title: فصل ۷ — اولین ورود و آشنایی با Dashboard
category: user-guide
language: fa
version: v0.3
audience: operator
---

# فصل ۷ — اولین ورود و آشنایی با Dashboard

## هدف این فصل

در فصل قبل CCC را نصب کردیم.

اکنون برای اولین بار وارد Dashboard خواهیم شد.

در پایان این فصل:

✓ وارد CCC خواهید شد.

✓ ساختار Dashboard را خواهید شناخت.

✓ تفاوت بخش‌های Dashboard ،System و Settings را خواهید دانست.

✓ با Contribution Advisor آشنا خواهید شد.

✓ مفهوم Traffic ،Regions و Node Status را خواهید فهمید.

✓ خواهید دانست هر قابلیت در کدام بخش قرار دارد.

## 7.1 اولین ورود

**هدف**

ورود به Dashboard برای اولین بار.

**آدرس Dashboard**

اگر در فصل قبل از:

conduit.example.com

استفاده کرده باشید:

Dashboard از طریق:

https://conduit.example.com

در دسترس خواهد بود.

**صفحه Login**

پس از باز کردن آدرس فوق صفحه Login نمایش داده می‌شود.

**اطلاعات ورود**

از اطلاعاتی استفاده کنید که هنگام نصب تعریف کرده‌اید:

Admin Username

Admin Password

![صفحهٔ ورود CCC](../../screenshots/login-page.png)

*صفحهٔ ورود CCC.*

## 7.2 امنیت ورود

**هدف**

درک رفتار امنیتی سیستم.

**تعداد تلاش‌های ناموفق**

CCC از حملات حدس رمز عبور محافظت می‌کند.

**محدودیت**

پس از:

5

تلاش ناموفق:

حساب برای:

15 Minutes

قفل می‌شود.

**اگر قفل شدید چه کار کنید؟**

از طریق SSH به Raspberry Pi متصل شوید.

اجرای دستور:

sudo ccc-unlock admin

**نکته مهم**

به جای:

admin

باید نام واقعی کاربر مدیر را وارد کنید.

**اعتبارسنجی**

پس از Unlock باید بتوانید دوباره Login کنید.

## 7.3 ساختار Dashboard

**هدف**

درک ساختار اصلی رابط کاربری.

CCC یک برنامه تک‌صفحه‌ای است.

یعنی هنگام جابجایی بین بخش‌ها معمولاً صفحه دوباره بارگذاری نمی‌شود.

**منوی اصلی**

نسخه v0.3.0 فقط سه بخش اصلی دارد:

Dashboard

System

Settings

![داشبورد CCC](../../screenshots/dashboard-overview.png)

*داشبورد CCC پس از ورود — وضعیت نود، مشاور، ترافیک و مناطق تجمیعی، همگی در مرورگر.*

## 7.4 بخش Dashboard

**هدف**

مشاهده وضعیت کلی ایستگاه Conduit.

این بخش معمولاً صفحه‌ای است که بیشترین زمان را در آن سپری خواهید کرد.

**اجزای اصلی**

Contribution Advisor

Node Status

Traffic

Lifetime & History

Traffic History

Regions

## 7.5 Contribution Advisor

**هدف**

کمک به بهینه‌سازی میزان مشارکت ایستگاه.

Advisor به صورت خودکار وضعیت ایستگاه را تحلیل می‌کند.

**موارد بررسی شده**

نمونه:

CPU Usage

RAM Usage

Temperature

Client Capacity

Activity Patterns

**انواع پیشنهادها**

از کم‌اهمیت تا مهم:

Info

Suggestion

Strong Suggestion

Warning

**مثال**

اگر دمای Raspberry Pi زیاد شود:

Advisor ممکن است هشدار نمایش دهد.

اگر ظرفیت کلاینت‌ها نزدیک سقف باشد:

ممکن است پیشنهاد افزایش ظرفیت نمایش داده شود.

**نکته مهم**

Advisor فقط پیشنهاد می‌دهد.

هیچ تغییری به صورت خودکار اعمال نمی‌شود.

## 7.6 Node Status

**هدف**

نمایش وضعیت فعلی Conduit.

**وضعیت‌های ممکن**

Live

Starting

Disconnected

Offline

Unknown

**معنی وضعیت‌ها**

**Live**

Conduit در حال اجرا و پاسخگو است.

**Starting**

سرویس در حال راه‌اندازی است.

**Disconnected**

ارتباط با سرویس برقرار نیست.

**Offline**

سرویس فعال نیست.

**Unknown**

وضعیت قابل تشخیص نیست.

## 7.7 Traffic

**هدف**

نمایش ترافیک نشست فعلی.

این بخش فقط ترافیک از زمان راه‌اندازی فعلی Conduit را نمایش می‌دهد.

**اطلاعات نمایش داده شده**

Upload

Download

**نکته مهم**

با Restart شدن Conduit این اعداد ریست می‌شوند.

## 7.8 Lifetime & History

**هدف**

نمایش آمار ماندگار.

برخلاف Traffic فعلی:

این بخش اطلاعات را در طول زمان نگهداری می‌کند.

**نمونه اطلاعات**

Lifetime Upload

Lifetime Download

**نکته مهم**

این قابلیت به Traffic Collector وابسته است.

اگر Traffic Collector فعال نباشد:

ممکن است داده‌ای مشاهده نکنید.

این به معنی خرابی CCC نیست.

## 7.9 Traffic History

**هدف**

نمایش روند فعالیت در طول زمان.

در این بخش نمودارهایی مشاهده می‌کنید که نشان می‌دهند:

Yesterday

Last Week

Historical Activity

**کاربرد**

کمک به تشخیص:

- روند رشد
- زمان‌های اوج فعالیت
- الگوهای استفاده

## 7.10 Regions

**هدف**

نمایش توزیع جغرافیایی ترافیک.

CCC فقط اطلاعات تجمیعی نمایش می‌دهد.

**نکته مهم**

CCC نمایش نمی‌دهد:

IP Addresses

User Identity

User Names

**فلسفه طراحی**

Aggregate Only

حفظ حریم خصوصی کاربران.

## 7.11 بخش System

**هدف**

نمایش سلامت سیستم.

در این بخش موارد زیر وجود دارند:

System Health

DDNS Status

Logs

## 7.12 System Health

**اطلاعات نمایش داده شده**

CPU

RAM

Temperature

Disk Usage

**کاربرد**

تشخیص سریع مشکلات سخت‌افزاری.

## 7.13 DDNS Status

**هدف**

بررسی وضعیت Cloudflare DDNS.

نمونه اطلاعات:

Last Update

Last Result

Consecutive Errors

**نتیجه‌های متداول**

updated

no_change

error

## 7.14 Logs

**هدف**

مشاهده لاگ‌های Conduit.

در این بخش:

آخرین:

200

خط لاگ نمایش داده می‌شود.

**دکمه Refresh**

برای بارگذاری مجدد لاگ‌ها.

**نکته مهم**

اطلاعات حساس به صورت خودکار حذف یا پنهان می‌شوند.

## 7.15 بخش Settings

**هدف**

پیکربندی و مدیریت CCC.

در این بخش موارد زیر وجود دارند:

Change Password

Appearance

Backup & Restore

Conduit Configuration

Personal Mode

Ryve

## 7.16 Change Password

**هدف**

تغییر رمز عبور مدیر.

توصیه می‌شود پس از اولین ورود:

رمز عبور پیش‌فرض را تغییر دهید.

## 7.17 Appearance

**هدف**

انتخاب ظاهر رابط کاربری.

**حالت‌های موجود**

Dark

Light

System

**System**

مطابق تنظیمات سیستم عامل یا مرورگر.

## 7.18 Backup & Restore

**هدف**

پشتیبان‌گیری و بازیابی تنظیمات.

عملیات موجود:

Create Backup

Inspect Backup

Restore Backup

**نکته مهم**

Restore یک عملیات حساس است.

قبل از Restore:

ابتدا Backup بررسی می‌شود.

## 7.19 Conduit Configuration

**هدف**

مدیریت تنظیمات Conduit.

در این بخش دو مفهوم مهم وجود دارد.

**Configured**

تنظیماتی که ذخیره شده‌اند.

**Effective**

تنظیماتی که هم‌اکنون توسط Conduit استفاده می‌شوند.

**چرا ممکن است متفاوت باشند؟**

زیرا برخی تغییرات در راه‌اندازی بعدی اعمال می‌شوند.

## 7.20 Personal Mode

**هدف**

ایجاد فضای شخصی برای استفاده خصوصی.

در این بخش می‌توانید:

- Identity ایجاد کنید.
- QR Pairing تولید کنید.
- تعداد کلاینت‌های شخصی را تعیین کنید.

**وضعیت‌های متداول**

Not Set Up

Created

Active

## 7.21 Ryve

**هدف**

مدیریت Claim مربوط به Ryve.

این بخش امکان:

Generate Claim

Show QR

Remove Claim

را فراهم می‌کند.

**هشدار امنیتی**

QR تولیدشده باید مانند یک Secret در نظر گرفته شود.

## 7.22 خروج از سیستم

**هدف**

پایان نشست مدیریتی.

برای خروج:

Logout

را انتخاب کنید.

**توصیه**

روی رایانه‌های اشتراکی همیشه Logout کنید.

## 7.23 نتیجه این فصل

اکنون:

✓ وارد CCC شده‌اید.

✓ ساختار Dashboard را می‌شناسید.

✓ تفاوت Dashboard ،System و Settings را می‌دانید.

✓ با Advisor آشنا شده‌اید.

✓ وضعیت Node را می‌شناسید.

✓ محل Personal Mode را می‌دانید.

✓ محل Ryve را می‌دانید.

✓ محل Backup & Restore را می‌دانید.

**فصل بعد**

در فصل بعد Contribution Advisor را با جزئیات بیشتری بررسی خواهیم کرد و خواهیم دید چگونه پیشنهادهای آن را تفسیر کنیم.
