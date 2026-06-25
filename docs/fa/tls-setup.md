# راه‌اندازی گواهی TLS

این راهنما نصب گواهی را برای Conduit Control Center پوشش می‌دهد. دو مسیر
پشتیبانی‌شده وجود دارد:

| مسیر | دشواری | پشتیبانی نصب‌کننده |
|---|---|---|
| **Cloudflare Origin Certificate** (توصیه‌شده) | مبتدی | کامل — `install.sh` بررسی و نصب می‌کند |
| **Let's Encrypt** | پیشرفته | فقط دستی — `install.sh` نمی‌تواند گواهی LE را بررسی کند |

> **اگر این اولین بار شماست:** از مسیر Cloudflare Origin Certificate استفاده کنید.
> ساده‌تر، رایگان، با اعتبار ۱۵ ساله و به‌طور کامل توسط `install.sh` پشتیبانی
> می‌شود.

> **یادداشت مترجم:** این سند ترجمهٔ `docs/tls-setup.md` است. در صورت وجود اختلاف،
> نسخه انگلیسی مرجع نهایی محسوب می‌شود.

---

## Path A — Cloudflare Origin Certificate (توصیه‌شده)

### گواهی Origin از Cloudflare چیست؟

گواهی Origin از Cloudflare یک گواهی TLS است که توسط CA شرکت Cloudflare صادر
می‌شود. این گواهی ارتباط میان سرورهای لبهٔ Cloudflare و Pi شما را امن می‌کند.

**مهم:** این گواهی *فقط* توسط Cloudflare معتبر شناخته می‌شود، نه مستقیماً توسط
مرورگرها. وقتی بازدیدکننده‌ای داشبورد شما را باز می‌کند، مرورگر او به Cloudflare
متصل می‌شود (که یک گواهی عمومی مورد اعتماد مرورگر ارائه می‌دهد) و Cloudflare با
استفاده از گواهی Origin به Pi شما متصل می‌شود. به این حالت «Full (strict)» در
SSL/TLS گفته می‌شود.

اگر پراکسی Cloudflare غیرفعال شود (ابر خاکستری)، مرورگر مستقیماً به Pi شما متصل
می‌شود و گواهی Origin را با خطای `NET::ERR_CERT_AUTHORITY_INVALID` رد می‌کند.
پراکسی را روشن نگه دارید.

### Before You Begin — تنظیم حالت SSL/TLS در Cloudflare روی Full (strict)

پیش از ساخت گواهی Origin، حالت رمزنگاری zone خود را روی **Full (strict)** تنظیم
کنید. این کار به Cloudflare می‌گوید که گواهی Origin روی Pi شما را بررسی کند؛ بدون
آن، گواهی‌ای که در حال نصب آن هستید استفاده نخواهد شد.

ابتدا مطمئن شوید دامنهٔ شما در Cloudflare **Active** است.

![Domain Active in Cloudflare](../screenshots/cloudflare-domain-active.png)

*دامنهٔ شما با وضعیت «Active» در Cloudflare.*

دامنهٔ خود را باز کنید، سپس از منوی ناوبری سمت چپ **SSL/TLS** را انتخاب کنید.

![Domain Overview — open SSL/TLS](../screenshots/cloudflare-ssl-domain-overview.png)

*دامنهٔ خود را باز کنید، سپس به SSL/TLS بروید.*

در صفحهٔ **SSL/TLS → Overview** می‌توانید حالت رمزنگاری فعلی را ببینید.

![SSL/TLS Overview](../screenshots/cloudflare-ssl-overview.png)

*SSL/TLS Overview — حالت رمزنگاری فعلی.*

روی **Configure** کلیک کنید، **Full (strict)** را انتخاب کنید و **Save** را بزنید.

![Select Full (strict)](../screenshots/cloudflare-ssl-full-strict.png)

*گزینهٔ **Full (strict)** را انتخاب کنید، سپس Save.*

با تنظیم این حالت، اکنون می‌توانید گواهی Origin را بسازید.

### A.1 — ساخت گواهی

به [داشبورد Cloudflare](https://dash.cloudflare.com) وارد شوید، zone خود را
انتخاب کنید و به **SSL/TLS → Origin Server** بروید.

![SSL/TLS → Origin Server](../screenshots/cloudflare-origin-server.png)

*مسیر: SSL/TLS → Origin Server → Create Certificate.*

روی **Create Certificate** کلیک کنید. در گام نخست، اجازه دهید Cloudflare کلید
خصوصی و CSR را برای شما تولید کند و نوع کلید را روی **RSA (2048)** تنظیم کنید.

![Create Certificate — generate key](../screenshots/cloudflare-origin-create.png)

*اجازه دهید Cloudflare کلید خصوصی را تولید کند — نوع کلید **RSA (2048)**.*

سپس نام‌های میزبان (hostnames) که گواهی باید آن‌ها را پوشش دهد وارد کنید و دورهٔ
اعتبار را انتخاب کنید.

![Create Certificate — hostnames and validity](../screenshots/cloudflare-origin-configure.png)

*نام‌های میزبان (`example.com`، `*.example.com`) و اعتبار ۱۵ ساله.*

مقادیر فرم را به این صورت پر کنید:

| فیلد | مقدار |
|---|---|
| **Let Cloudflare generate a private key and CSR** | انتخاب‌شده (پیش‌فرض) |
| **Key type** | **RSA (2048)** |
| **Hostnames** | دامنه و wildcard شما، برای مثال `example.com, *.example.com` |
| **Certificate Validity** | ۱۵ سال (پیش‌فرض) |

> ⚠️ **نوع کلید باید RSA (2048) باشد. ECDSA را انتخاب نکنید.**
>
> نصب‌کننده (`install.sh` Phase 1h) کلید خصوصی را با `openssl rsa` بررسی می‌کند.
> این دستور فقط کلیدهای RSA را می‌خواند. اگر ECDSA را انتخاب کنید، نصب‌کننده کلید
> را با خطای OpenSSL رد می‌کند. اگر قبلاً یک گواهی ECDSA ساخته‌اید، آن را در
> داشبورد Cloudflare حذف کنید و یک گواهی جدید با RSA (2048) بسازید.

روی **Create** کلیک کنید. Cloudflare موارد زیر را نمایش می‌دهد:

- **Origin Certificate** — گواهی (عمومی، با `-----BEGIN CERTIFICATE-----` شروع
  می‌شود)
- **Private Key** — کلید خصوصی (محرمانه، با `-----BEGIN RSA PRIVATE KEY-----`
  شروع می‌شود)

Cloudflare اکنون گواهی و کلید خصوصی را در یک صفحهٔ واحد نمایش می‌دهد.

![Origin Certificate and Private Key display](../screenshots/cloudflare-origin-cert-key.png)

*گواهی و کلید خصوصی فقط یک‌بار نمایش داده می‌شوند — همین حالا هر دو را کپی کنید. (کلید در این نمونه پنهان‌سازی شده است.)*

⚠️ **کلید خصوصی فقط یک‌بار نمایش داده می‌شود.** اگر این صفحه را بدون کپی کردن آن
ترک کنید، دیگر نمی‌توانید آن را بازیابی کنید و باید یک گواهی جدید بسازید.

**این تب مرورگر را باز نگه دارید.** در گام بعد باید هر دو بلوک را کپی کنید.

### A.2 — ساخت پوشهٔ گواهی روی Pi

با SSH به Pi خود وارد شوید و اجرا کنید:

```bash
sudo mkdir -p /etc/conduit-cc/tls
sudo chmod 700 /etc/conduit-cc/tls
```

### A.3 — ذخیرهٔ گواهی و کلید روی Pi

باید دو بلوک متنی را از داشبورد Cloudflare به فایل‌هایی روی Pi خود منتقل کنید. دو
روش در زیر آمده است — هرکدام برایتان آسان‌تر است از آن استفاده کنید.

#### Method 1: جای‌گذاری مستقیم در nano (ساده‌ترین)

روی Pi خود، فایل گواهی را در nano باز کنید:

```bash
sudo nano /etc/conduit-cc/tls/origin.pem
```

در تب مرورگر Cloudflare، داخل کادر **Origin Certificate** کلیک کنید و کل متن را
انتخاب کنید (`Ctrl+A` یا `Cmd+A`). آن را کپی کنید (`Ctrl+C` یا `Cmd+C`).

در پنجرهٔ ترمینال nano، متن را جای‌گذاری کنید (`Ctrl+Shift+V` در بیشتر ترمینال‌های
لینوکس، یا `Cmd+V` در macOS). متن گواهی باید ظاهر شود و با
`-----BEGIN CERTIFICATE-----` شروع و با `-----END CERTIFICATE-----` پایان یابد.

ذخیره و خروج: `Ctrl+X` را بزنید، سپس `Y`، سپس `Enter`.

برای کلید خصوصی همین کار را تکرار کنید:

```bash
sudo nano /etc/conduit-cc/tls/origin.key
```

متن **Private Key** را از تب Cloudflare جای‌گذاری کنید (با
`-----BEGIN RSA PRIVATE KEY-----` شروع و با `-----END RSA PRIVATE KEY-----` پایان
می‌یابد).

ذخیره و خروج: `Ctrl+X` → `Y` → `Enter`.

#### Method 2: scp از رایانهٔ محلی شما

اگر ابتدا گواهی و کلید را روی لپ‌تاپ یا رایانهٔ خود به‌صورت فایل ذخیره کرده‌اید،
آن‌ها را با `scp` به Pi کپی کنید:

```bash
# Run these commands on your LOCAL machine, not the Pi
# Replace pi-hostname with your Pi's hostname or IP address

scp origin.pem your-user@pi-hostname:/tmp/origin.pem
scp origin.key your-user@pi-hostname:/tmp/origin.key
```

سپس روی Pi، فایل‌ها را به محل خود منتقل کنید:

```bash
sudo mv /tmp/origin.pem /etc/conduit-cc/tls/origin.pem
sudo mv /tmp/origin.key /etc/conduit-cc/tls/origin.key
```

#### Method 3: WinSCP در ویندوز (بدون ترمینال)

اگر روی ویندوز هستید و ترجیح می‌دهید از ترمینال استفاده نکنید، می‌توانید دو بلوک
را در Notepad به‌صورت فایل ذخیره کنید و با WinSCP بارگذاری کنید.

ابتدا هر بلوک را از تب Cloudflare با **Notepad → File → Save As** ذخیره کنید. در
پنجرهٔ Save، گزینهٔ **Save as type** را روی **All Files** و **Encoding** را روی
**UTF-8** قرار دهید و نام دقیق فایل را تایپ کنید:

- بلوک **Origin Certificate** را با نام `origin.pem` ذخیره کنید
- بلوک **Private Key** را با نام `origin.key` ذخیره کنید

قرار دادن **Save as type** روی **All Files** همان چیزی است که از افزوده‌شدن پسوند
پنهان `.txt` توسط Notepad جلوگیری می‌کند.

> ⚠️ **کاربران ویندوز:** مطمئن شوید نام فایل‌ها دقیقاً این‌گونه است:
>
> `origin.pem`  `origin.key`
>
> و **نه**:
>
> `origin.pem.txt`  `origin.key.txt`
>
> پسوند اضافی `.txt` باعث می‌شود نصب‌کننده فایل‌ها را رد کند.

سپس آن‌ها را به Pi منتقل کنید:

1. **WinSCP** را باز کنید و یک **New Site** بسازید.
2. **File protocol** را روی **SFTP**، **Host name** را روی IP یا hostname مربوط
   به Pi، **Port** را روی `22` تنظیم کنید و نام کاربری و رمز عبور خود را وارد کنید.
3. متصل شوید، سپس `origin.pem` و `origin.key` را داخل **`/tmp`** روی Pi بکشید.

در نهایت، روی Pi، فایل‌ها را به محل خود منتقل کنید:

```bash
sudo mv /tmp/origin.pem /etc/conduit-cc/tls/origin.pem
sudo mv /tmp/origin.key /etc/conduit-cc/tls/origin.key
```

سپس مالکیت و مجوزها را با استفاده از بخش **A.4** در ادامه تنظیم کنید.

### A.4 — تنظیم مجوزهای فایل

```bash
sudo chmod 644 /etc/conduit-cc/tls/origin.pem
sudo chmod 600 /etc/conduit-cc/tls/origin.key
```

گواهی (`.pem`) می‌تواند برای همه قابل خواندن باشد. کلید خصوصی (`.key`) باید فقط
توسط root قابل خواندن باشد.

### A.5 — بررسی فایل‌ها

هر سه بررسی را اجرا کنید. هرکدام باید بدون خطا کامل شود.

**بررسی ۱ — گواهی قابل خواندن است و صادرکنندهٔ درست را نشان می‌دهد:**

```bash
openssl x509 -noout -issuer -dates -in /etc/conduit-cc/tls/origin.pem
```

خروجی مورد انتظار شامل موارد زیر خواهد بود:

```
issuer=C=US, O=Cloudflare, Inc., CN=Cloudflare Inc ECC CA-3
notBefore=...
notAfter=...
```

صادرکننده (issuer) باید شامل `Cloudflare` باشد. اگر صادرکننده `R3`، `E1` یا
`ISRG Root X1` را نشان دهد، این یک گواهی Let's Encrypt است — نصب‌کننده آن را در
Phase 1g رد می‌کند.

**بررسی ۲ — کلید خصوصی از نوع RSA و معتبر است:**

```bash
openssl rsa -noout -check -in /etc/conduit-cc/tls/origin.key
```

خروجی مورد انتظار:

```
RSA key ok
```

اگر `unable to load Private Key` یا `no start line` می‌بینید، یا فایل خالی است یا
نوع کلید RSA نیست. گواهی را با نوع کلید RSA (2048) دوباره بسازید.

**بررسی ۳ — گواهی و کلید یک جفت متناظر هستند:**

```bash
# These two commands must produce the same hash
openssl x509 -noout -modulus -in /etc/conduit-cc/tls/origin.pem | openssl md5
openssl rsa  -noout -modulus -in /etc/conduit-cc/tls/origin.key  | openssl md5
```

هر دو خط باید هش MD5 یکسانی چاپ کنند. اگر متفاوت باشند، گواهی و کلید یک جفت
متناظر نیستند — ممکن است کلید اشتباهی را جای‌گذاری کرده باشید. از داشبورد
Cloudflare دوباره جای‌گذاری کنید.

### A.6 — مسیرهای پیش‌فرض فایل

نصب‌کننده و پیکربندی nginx از این مسیرهای پیش‌فرض استفاده می‌کنند:

| فایل | مسیر پیش‌فرض |
|---|---|
| گواهی | `/etc/conduit-cc/tls/origin.pem` |
| کلید خصوصی | `/etc/conduit-cc/tls/origin.key` |

اگر می‌خواهید از مسیرهای متفاوتی استفاده کنید، آن‌ها را در `/etc/conduit-cc/.env`
تنظیم کنید:

```env
TLS_CERT_PATH=/your/custom/path/to/cert.pem
TLS_KEY_PATH=/your/custom/path/to/key.key
```

> **توجه:** تغییر این مسیرها مستلزم به‌روزرسانی دستورهای `ssl_certificate` و
> `ssl_certificate_key` در پیکربندی nginx (`deployment/conduit-cc.nginx`) نیز
> هست. نصب‌کننده در حال حاضر از مسیرهای سفارشی TLS پشتیبانی نمی‌کند — مگر دلیل
> خاصی دارید، از مقادیر پیش‌فرض استفاده کنید.

### A.7 — بازگشت به pre-install.md

با قرار گرفتن گواهی و کلید در محل خود و موفقیت هر سه بررسی، به
[`docs/pre-install.md`](../pre-install.md) گام ۶ بازگردید و پیش از اجرای
`install.sh` فهرست نهایی را کامل کنید.

---

## Path B — Let's Encrypt (پیشرفته، فقط دستی)

> ⚠️ **Let's Encrypt توسط `install.sh` پشتیبانی نمی‌شود.**
>
> نصب‌کننده گواهی‌های TLS را با بررسی این‌که صادرکننده شامل `"Cloudflare"` باشد
> اعتبارسنجی می‌کند (Phase 1g). گواهی‌های Let's Encrypt توسط `R3` یا `E1` صادر
> می‌شوند و همیشه در این بررسی شکست می‌خورند. اگر `install.sh` را با یک گواهی
> Let's Encrypt اجرا کنید، نصب‌کننده در Phase 1g متوقف می‌شود.
>
> Let's Encrypt در اینجا به‌عنوان یک جایگزین دستی برای کاربرانی ارائه شده که
> نمی‌توانند یا نمی‌خواهند از پراکسی Cloudflare استفاده کنند. باید nginx را خودتان
> پیکربندی کنید و پرسش‌های TLS مربوط به `install.sh` را اجرا نکنید.

### چه زمانی از Let's Encrypt استفاده کنیم

- Pi شما یک IP عمومی دارد و می‌خواهید مرورگرها بدون پراکسی Cloudflare به گواهی
  اعتماد کنند
- مستقیماً متصل می‌شوید (بدون CDN) و یک گواهی مورد اعتماد مرورگر می‌خواهید
- می‌دانید که مسئولیت تمدید گواهی و پیکربندی nginx بر عهدهٔ شماست

### B.1 — پیش‌نیازهای Let's Encrypt

- رکورد DNS A دامنهٔ شما مستقیماً به IP مربوط به Pi اشاره می‌کند (برای چالش ACME،
  پراکسی باید **خاموش / ابر خاکستری** باشد)
- پورت 80 روی فایروال Pi و روتر شما باز است
- `certbot` نصب شده است: `sudo apt install certbot python3-certbot-nginx`

### B.2 — دریافت گواهی

> ⚠️ **از `certbot certonly --nginx` استفاده کنید، نه `certbot --nginx`.**
>
> افزونهٔ `--nginx` (بدون `certonly`) بلوک سرور nginx را به‌طور خودکار تغییر
> می‌دهد و پیکربندی سفارشی nginx نصب‌شده توسط این پروژه را بازنویسی می‌کند (که شامل
> بازیابی real-IP مربوط به Cloudflare، محدودسازی نرخ و هدرهای امنیتی است). همیشه از
> `certonly` استفاده کنید تا certbot فقط گواهی را بگیرد و به پیکربندی nginx دست
> نزند.

```bash
sudo certbot certonly --nginx -d conduit.example.com
```

`conduit.example.com` را با نام دامنهٔ واقعی خود جایگزین کنید.

Certbot گواهی و کلید را در این مسیر قرار می‌دهد:

```
/etc/letsencrypt/live/conduit.example.com/fullchain.pem
/etc/letsencrypt/live/conduit.example.com/privkey.pem
```

### B.3 — پیکربندی دستی nginx

پیکربندی nginx را ویرایش کنید تا به مسیرهای گواهی Let's Encrypt اشاره کند:

در `/etc/nginx/sites-available/conduit-cc`، دستورهای TLS را به‌روزرسانی کنید:

```nginx
ssl_certificate     /etc/letsencrypt/live/conduit.example.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/conduit.example.com/privkey.pem;
```

اگر از قالب `deployment/conduit-cc.nginx` نصب کرده‌اید، خطوط `ssl_certificate` و
`ssl_certificate_key` را با مسیرهای بالا جایگزین کنید.

اگر می‌خواهید برنامه از محل جدید گواهی مطلع باشد، مسیرهای پیش‌فرض قالب پیکربندی
nginx را از طریق `.env` به‌روزرسانی کنید:

```env
TLS_CERT_PATH=/etc/letsencrypt/live/conduit.example.com/fullchain.pem
TLS_KEY_PATH=/etc/letsencrypt/live/conduit.example.com/privkey.pem
```

nginx را آزمایش و بارگذاری مجدد کنید:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### B.4 — تمدید گواهی

گواهی‌های Let's Encrypt پس از ۹۰ روز منقضی می‌شوند. تمدید خودکار را راه‌اندازی
کنید:

```bash
sudo systemctl enable --now certbot.timer
```

فعال بودن timer را بررسی کنید:

```bash
sudo systemctl status certbot.timer
```

### B.5 — Let's Encrypt و اسکریپت DDNS

اسکریپت DDNS (`scripts/cloudflare-ddns.sh`) مستقل از مسیر TLS است و با
Let's Encrypt به همان شکل کار می‌کند. اما:

- با Let's Encrypt، پراکسی Cloudflare باید برای چالش ACME **خاموش (ابر خاکستری)**
  باشد. برای حفظ گواهی مورد اعتماد مرورگر آن را خاموش نگه دارید.
- با خاموش بودن پراکسی، اسکریپت DDNS همچنان رکورد A شما را به IP فعلی Pi
  به‌روزرسانی می‌کند — این برای دقیق نگه‌داشتن DNS همچنان مفید است.

### B.6 — پشتیبانی نصب‌کننده در آینده

نسخه‌ای آینده از `install.sh` ممکن است مسیر Let's Encrypt را اضافه کند. تا آن
زمان، کاربران Let's Encrypt مسئول پیکربندی nginx و تمدید گواهی خود هستند.

---

## انتخاب بین دو مسیر

| | Cloudflare Origin Certificate | Let's Encrypt |
|---|---|---|
| مورد اعتماد مرورگرها | فقط از طریق پراکسی Cloudflare | بله (مستقیم) |
| انقضا | پس از ۱۵ سال | پس از ۹۰ روز |
| تمدید خودکار | لازم نیست | لازم است |
| پشتیبانی نصب‌کننده | کامل | ندارد |
| نیاز به پراکسی Cloudflare | بله | خیر (پراکسی باید خاموش باشد) |
| دشواری | مبتدی | پیشرفته |

برای یک Raspberry Pi پشت پراکسی Cloudflare — که فرض این پروژه است — مسیر
Cloudflare Origin Certificate انتخاب درست است.
