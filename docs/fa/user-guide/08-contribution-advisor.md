---
title: فصل ۸ — آشنایی با Contribution Advisor
category: user-guide
language: fa
version: v0.3
audience: operator
---

# فصل ۸ — آشنایی با Contribution Advisor

## هدف این فصل

در فصل قبل با Dashboard آشنا شدیم.

یکی از مهم‌ترین بخش‌های Dashboard Contribution Advisor است.

Advisor به شما کمک می‌کند وضعیت ایستگاه خود را بهتر درک کنید و تصمیم‌های آگاهانه‌تری بگیرید.

در پایان این فصل خواهید دانست:

✓ Contribution Advisor چیست

✓ چگونه پیشنهادها تولید می‌شوند

✓ معنی سطوح مختلف هشدار چیست

✓ چگونه پیشنهادهای Capacity را تفسیر کنید

✓ چگونه پیشنهادهای Reduced Mode را تفسیر کنید

✓ چگونه هشدارهای Health را تفسیر کنید

✓ چه کارهایی Advisor انجام نمی‌دهد

## 8.1 Contribution Advisor چیست؟

**هدف**

درک نقش Advisor در CCC.

Advisor یک سیستم تحلیلی است که:

- وضعیت سیستم را بررسی می‌کند
- وضعیت Conduit را بررسی می‌کند
- الگوی ترافیک را بررسی می‌کند

و سپس پیشنهادهایی ارائه می‌دهد.

**نکته مهم**

Advisor یک سیستم تصمیم‌گیری خودکار نیست.

**قانون مهم**

Advisor

≠

Automation

Advisor فقط پیشنهاد می‌دهد.

هیچ تغییری را به صورت خودکار اعمال نمی‌کند.

## 8.2 فلسفه طراحی Advisor

**هدف**

درک رویکرد طراحی Advisor.

Advisor بر اساس سه اصل طراحی شده است:

**اصل اول**

کمک به افزایش مشارکت

**اصل دوم**

جلوگیری از فشار بیش از حد به Raspberry Pi

**اصل سوم**

حفظ سادگی

**مثال**

Advisor ممکن است پیشنهاد دهد:

Raise Client Limit

اما این تغییر را خودش اعمال نمی‌کند.

کاربر باید:

- پیشنهاد را بررسی کند
- تصمیم بگیرد
- در صورت تمایل تنظیمات را تغییر دهد

## 8.3 اطلاعاتی که Advisor بررسی می‌کند

**هدف**

شناخت داده‌های مورد استفاده.

Advisor داده‌های زیر را بررسی می‌کند.

**منابع سیستمی**

CPU Usage

RAM Usage

CPU Temperature

**وضعیت Conduit**

Connected Clients

Maximum Clients

Idle Time

Broker Status

Uptime

**اطلاعات ترافیک**

Lifetime Upload

Lifetime Download

Last 24 Hours

Last 7 Days

Hourly History

**نکته مهم**

تمام اطلاعات:

Aggregate Only

هستند.

Advisor هیچ‌گاه:

- IP کاربران
- هویت کاربران
- فعالیت کاربران منفرد

را بررسی نمی‌کند.

## 8.4 سطوح اهمیت پیشنهادها

**هدف**

درک Severity Levels.

Advisor چهار سطح اهمیت دارد.

**Warning**

بالاترین سطح اهمیت.

نمونه:

CPU Too High

Broker Disconnected

**Recommended**

پیشنهاد قوی.

نمونه:

Raise Client Limit

در شرایط بسیار مناسب.

**Suggestion**

پیشنهاد معمولی.

نمونه:

Consider Reduced Mode

**Info**

اطلاعات عمومی.

نمونه:

New Station

## 8.5 پیشنهادهای Capacity

**هدف**

درک پیشنهادهای مربوط به ظرفیت.

Capacity تعیین می‌کند:

چند کلاینت می‌توانند همزمان از ایستگاه شما استفاده کنند.

### 8.5.1 چه زمانی هشدار کاهش ظرفیت داده می‌شود؟

اگر هر یک از شرایط زیر برقرار باشد:

CPU > 90%

RAM > 85%

Temperature ≥ 80°C

Advisor هشدار می‌دهد:

Reduce Client Limit

**دلیل**

جلوگیری از ناپایداری سیستم.

### 8.5.2 چه زمانی پیشنهاد افزایش ظرفیت داده می‌شود؟

Advisor فقط زمانی پیشنهاد افزایش ظرفیت می‌دهد که:

**Conduit فعال باشد**

Live

**تقاضا بالا باشد**

Connected Clients

≥

80% Of Capacity

**CPU پایین باشد**

CPU < 40%

**RAM پایین باشد**

RAM < 70%

**دما مناسب باشد**

Temperature < 70°C

اگر سنسور دما وجود نداشته باشد این شرط نادیده گرفته می‌شود.

**شرایط پایدار باشند**

Advisor فقط وضعیت لحظه‌ای را بررسی نمی‌کند.

باید برای مدتی شرایط مناسب حفظ شده باشند.

**زمان کافی گذشته باشد**

بین دو پیشنهاد افزایش ظرفیت:

24 Hours

فاصله وجود دارد.

### 8.5.3 فرمول افزایش ظرفیت

Advisor از فرمول زیر استفاده می‌کند:

Current Limit

+

25%

اما:

**حداقل افزایش**

25 Clients

**حداکثر افزایش**

100 Clients

**سقف نهایی**

1000 Clients

**مثال**

اگر:

Current Limit = 200

Advisor پیشنهاد می‌دهد:

250

اگر:

Current Limit = 600

Advisor پیشنهاد می‌دهد:

700

## 8.6 پیشنهادهای Reduced Mode

**هدف**

درک پیشنهادهای مربوط به ساعات کم‌ترافیک.

Reduced Mode قابلیتی در Conduit است که در ساعات کم‌ترافیک محدودیت‌های محافظه‌کارانه‌تری اعمال می‌کند.

**Advisor چگونه تصمیم می‌گیرد؟**

Advisor حداقل:

7 Days

تاریخچه نیاز دارد.

اگر داده کافی وجود نداشته باشد:

هیچ پیشنهادی ارائه نمی‌شود.

**تحلیل ساعات کم‌ترافیک**

Advisor:

- فعالیت ساعتی را بررسی می‌کند
- Median را محاسبه می‌کند
- ساعات کم‌ترافیک را پیدا می‌کند

سپس طولانی‌ترین بازه آرام را پیشنهاد می‌دهد.

**مثال**

ممکن است پیشنهاد شود:

01:00 UTC

to

07:00 UTC

**شدت پیشنهاد**

اگر بازه بسیار پایدار باشد:

Recommended

در غیر این صورت:

Suggestion

**نکته مهم**

Advisor فقط بازه را پیشنهاد می‌دهد.

فعال‌سازی Reduced Mode همچنان توسط کاربر انجام می‌شود.

## 8.7 ارزیابی سلامت سیستم

**هدف**

درک وضعیت‌های Health.

Advisor همیشه یک Summary نمایش می‌دهد.

**Live**

Broker Live

و سیستم سالم است.

**Disconnected**

ارتباط با Broker برقرار نیست.

**Offline**

داده‌ای از Conduit دریافت نمی‌شود.

**Unknown**

وضعیت قابل تشخیص نیست.

## 8.8 هشدارهای Health

**Broker Disconnected**

سطح:

Warning

**Contribution Dropping**

اگر فعالیت اخیر به شکل محسوسی کمتر از میانگین قبلی باشد.

سطح:

Warning

**New Station**

ایستگاه تازه راه‌اندازی شده است.

سطح:

Info

**No Recent Traffic**

برای مدت طولانی فعالیت مشاهده نشده است.

سطح:

Suggestion

## 8.9 Advisor هر چند وقت یک بار به‌روزرسانی می‌شود؟

**هدف**

درک رفتار Refresh.

Dashboard در زمان فعال بودن:

هر:

60 Seconds

Advisor را به‌روزرسانی می‌کند.

**نکته مهم**

اگر Dashboard باز نباشد:

Advisor Poll نمی‌شود.

## 8.10 Advisor چه کارهایی انجام نمی‌دهد؟

**هدف**

جلوگیری از سوءبرداشت.

Advisor:

❌ تنظیمات را تغییر نمی‌دهد.

❌ Capacity را افزایش نمی‌دهد.

❌ Reduced Mode را فعال نمی‌کند.

❌ سرویس‌ها را Restart نمی‌کند.

❌ Conduit را تغییر نمی‌دهد.

Advisor فقط:

Observe

Analyze

Recommend

## 8.11 مثال‌های واقعی

**مثال ۱**

CPU:

95%

Advisor:

Warning

Reduce Client Limit

**مثال ۲**

Clients:

90%

Of Capacity

CPU:

20%

RAM:

35%

Advisor:

Recommended

Raise Client Limit

**مثال ۳**

7 روز تاریخچه موجود است.

هر شب فعالیت بسیار کم است.

Advisor:

Reduced Mode Suggestion

## 8.12 نتیجه این فصل

اکنون می‌دانید:

✓ Advisor چگونه کار می‌کند.

✓ Capacity Recommendation چیست.

✓ Reduced Mode Recommendation چیست.

✓ Health Recommendation چیست.

✓ معنی Severity Levels چیست.

✓ Advisor چه کارهایی انجام نمی‌دهد.

**فصل بعد**

در فصل بعد بخش:

Conduit Configuration

را بررسی خواهیم کرد و یاد خواهیم گرفت چگونه پیشنهادهای Advisor را در عمل اعمال کنیم.
