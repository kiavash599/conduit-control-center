---
title: فصل ۱۰ — Personal Mode
category: user-guide
language: fa
version: v0.3
audience: operator
---

# فصل ۱۰ — Personal Mode

## هدف این فصل

تا اینجا با:

- Dashboard
- Contribution Advisor
- Conduit Configuration

آشنا شدیم.

اکنون به یکی از قابلیت‌های اختصاصی CCC می‌رسیم:

Personal Mode

Personal Mode به شما اجازه می‌دهد یک فضای خصوصی برای استفاده شخصی یا اشتراک‌گذاری محدود با افراد مورد اعتماد ایجاد کنید.

## در پایان این فصل

✓ مفهوم Personal Mode را درک خواهید کرد.

✓ مفهوم Identity را خواهید شناخت.

✓ QR Pairing را خواهید شناخت.

✓ Max Personal Clients را خواهید شناخت.

✓ فعال‌سازی و غیرفعال‌سازی را یاد خواهید گرفت.

✓ Regenerate و Restore را خواهید شناخت.

✓ ملاحظات امنیتی را خواهید دانست.

✓ محدودیت‌های Backup را خواهید شناخت.

## 10.1 Personal Mode چیست؟

**هدف**

درک فلسفه Personal Mode.

در حالت عادی:

Conduit ظرفیت خود را در اختیار کاربران عمومی قرار می‌دهد.

Personal Mode به شما اجازه می‌دهد بخشی از ظرفیت را برای استفاده شخصی یا گروهی محدود اختصاص دهید.

**نکته مهم**

Personal Mode یک ویژگی مستقل از کاربران عمومی است.

## 10.2 دو بخش اصلی Personal Mode

**هدف**

درک معماری واقعی قابلیت.

Personal Mode از دو بخش مستقل تشکیل شده است:

Identity (Compartment)

و

Max Personal Clients

**نکته مهم**

این دو بخش مستقل هستند.

## 10.3 Identity چیست؟

**هدف**

شناخت هویت Personal Mode.

Identity یک شناسه منحصر‌به‌فرد است که توسط Conduit ایجاد می‌شود.

این شناسه:

Compartment

نامیده می‌شود.

**محل ذخیره‌سازی**

Conduit آن را در سیستم ذخیره می‌کند.

**ویژگی‌ها**

✓ منحصر‌به‌فرد

✓ پایدار

✓ بدون تاریخ انقضا

✓ قابل بازتولید

## 10.4 ایجاد Identity

**هدف**

ساخت اولین Identity.

در Settings → Personal Mode:

گزینه:

Create Identity

را انتخاب کنید.

سپس:

یک Display Name وارد کنید.

**مثال**

Kiavash Personal Access

**نکته**

Display Name محرمانه نیست.

فقط برای تشخیص بهتر استفاده می‌شود.

## 10.5 ایجاد Identity به معنی فعال شدن نیست

**هشدار مهم**

⚠️

بسیاری از کاربران تصور می‌کنند:

Identity Created

=

Personal Mode Active

این تصور اشتباه است.

واقعیت:

Identity Created

+

Max Personal Clients > 0

=

Personal Mode Active

## 10.6 وضعیت‌های قابل مشاهده

**هدف**

درک وضعیت‌های رابط کاربری.

**Not Set Up**

هیچ Identity وجود ندارد.

**Created – Inactive**

Identity ساخته شده است.

اما:

Max Personal Clients = 0

است.

**Active**

Identity وجود دارد.

و:

Max Personal Clients > 0

است.

## 10.7 QR Pairing چیست؟

**هدف**

آشنایی با روش اشتراک‌گذاری دسترسی.

پس از ایجاد Identity:

CCC می‌تواند یک QR Code تولید کند.

این QR برای Pairing استفاده می‌شود.

**نکته مهم**

QR از روی Identity ساخته می‌شود.

## 10.8 QR Code شامل چه اطلاعاتی است؟

**هدف**

درک اهمیت امنیتی QR.

QR شامل اطلاعات Identity است.

از جمله:

Compartment Identity

Display Name

**هشدار بسیار مهم**

⚠️

QR Code یک Credential محسوب می‌شود.

با آن مانند رمز عبور رفتار کنید.

فقط با افراد مورد اعتماد به اشتراک بگذارید.

## 10.9 آیا QR منقضی می‌شود؟

**پاسخ کوتاه**

خیر.

**نکته مهم**

QR دارای زمان انقضا نیست.

تا زمانی که Identity تغییر نکند:

QR معتبر باقی می‌ماند.

## 10.10 نمایش QR

**هدف**

درک نحوه نمایش.

CCC QR را در مرورگر تولید می‌کند.

QR فقط به صورت موقت نمایش داده می‌شود.

پس از بستن پنجره:

نمایش QR حذف می‌شود.

**نکته**

CCC QR را در پایگاه داده ذخیره نمی‌کند.

## 10.11 Max Personal Clients

**هدف**

تعیین ظرفیت Personal Mode.

این مقدار تعیین می‌کند چند کلاینت شخصی می‌توانند همزمان استفاده کنند.

**محدوده مجاز**

0

to

1000

## 10.12 مقدار صفر چه معنایی دارد؟

**هدف**

درک نحوه غیرفعال‌سازی.

اگر:

Max Personal Clients = 0

باشد:

Personal Mode غیرفعال است.

**نکته مهم**

Identity حذف نمی‌شود.

فقط استفاده از آن متوقف می‌شود.

## 10.13 فعال‌سازی Personal Mode

**هدف**

فعال کردن قابلیت.

مراحل:

Create Identity

↓

Set Max Personal Clients

↓

Apply

پس از Apply:

Conduit Restart می‌شود.

## 10.14 Restart Behavior

**هدف**

درک زمان‌های Restart.

**نیاز به Restart**

✓ تغییر Max Personal Clients

✓ Regenerate Identity (در حالت فعال)

✓ Restore Identity (در حالت فعال)

**بدون Restart**

✓ ایجاد Identity

✓ مشاهده QR

✓ مشاهده وضعیت

## 10.15 Regenerate چیست؟

**هدف**

ایجاد Identity جدید.

Regenerate یعنی:

Create New Identity

**نتیجه**

تمام QRهای قبلی نامعتبر می‌شوند.

QR جدید تولید خواهد شد.

**هشدار**

⚠️

قبل از Regenerate مطمئن شوید افراد مورد نیاز QR جدید را دریافت خواهند کرد.

## 10.16 Restore Previous Identity

**هدف**

بازگرداندن Identity قبلی.

CCC یک نسخه پشتیبان داخلی از Identity قبلی نگهداری می‌کند.

در صورت نیاز:

می‌توانید Identity قبلی را بازیابی کنید.

**کاربرد**

مثال:

Regenerate Performed By Mistake

## 10.17 Backup و Personal Mode

**هدف**

درک محدودیت مهم Backup.

**هشدار بسیار مهم**

⚠️

Backup همه چیز را ذخیره نمی‌کند.

**موارد ذخیره‌شده**

✓ Display Name

✓ Max Personal Clients

**موارد ذخیره‌نشده**

✗ Identity

✗ Compartment File

**نتیجه**

پس از Restore:

ممکن است لازم باشد Identity جدید ایجاد کنید.

## 10.18 بعد از Restore چه اتفاقی می‌افتد؟

**مثال**

قبل از Backup:

Display Name

=

Kiavash Personal

Max Personal Clients

=

5

پس از Restore:

همین مقادیر باز می‌گردند.

اما Identity واقعی بازگردانده نمی‌شود.

بنابراین:

QRهای قدیمی معتبر نخواهند بود.

## 10.19 امنیت Personal Mode

**هدف**

آشنایی با بهترین شیوه‌ها.

**توصیه 1**

QR را فقط با افراد مورد اعتماد به اشتراک بگذارید.

**توصیه 2**

از ذخیره QR در مکان‌های عمومی خودداری کنید.

**توصیه 3**

در صورت احتمال افشای QR:

Regenerate

انجام دهید.

**توصیه 4**

Display Name را اطلاعات محرمانه در نظر نگیرید.

**توصیه 5**

Identity را به عنوان یک Secret مهم در نظر بگیرید.

## 10.20 عیب‌یابی

**Personal Mode فعال نمی‌شود**

بررسی کنید:

Max Personal Clients > 0

باشد.

**QR نمایش داده نمی‌شود**

بررسی کنید:

Identity ایجاد شده باشد.

**QR قدیمی کار نمی‌کند**

ممکن است:

Regenerate

انجام شده باشد.

**پس از Restore مشکل وجود دارد**

ممکن است نیاز باشد Identity جدید ایجاد شود.

## 10.21 نتیجه این فصل

اکنون می‌دانید:

✓ Personal Mode چیست.

✓ Identity چیست.

✓ QR Pairing چیست.

✓ چگونه فعال یا غیرفعال می‌شود.

✓ Regenerate چه می‌کند.

✓ Restore چه می‌کند.

✓ چه چیزی در Backup ذخیره می‌شود.

✓ چه چیزی در Backup ذخیره نمی‌شود.

✓ چگونه امنیت Personal Mode را حفظ کنید.

**فصل بعد**

در فصل بعد:

Ryve Integration

را بررسی خواهیم کرد و یاد خواهیم گرفت چگونه Claim QR ایجاد کنیم و از آن استفاده کنیم.
