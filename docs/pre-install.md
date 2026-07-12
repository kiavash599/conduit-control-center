# Pre-Installation: Cloudflare Setup

> **Complete all steps on this page before running `install.sh`.**
>
> The installer validates each item below via the Cloudflare API. If any step
> is missing or incorrect, the installer will stop and tell you which check
> failed.

---

## What this guide covers

`install.sh` collects four pieces of information during Phase 1 and validates
each one against the Cloudflare API before making any changes to your Pi:

| Installer prompt | What it validates |
|---|---|
| Cloudflare API token | Token is valid and has the required permissions |
| Zone name (e.g. `example.com`) | Zone exists in your account |
| DNS record name (e.g. `conduit.example.com`) | A-type record exists and proxy is ON |
| TLS certificate + key paths | Files exist, cert is issued by Cloudflare CA, key is RSA |

Complete the six steps below in order.

---

## Step 1 — Confirm prerequisites

You need:

- A **registered domain name** with Cloudflare as the DNS provider (not just
  the registrar — Cloudflare must be managing the DNS nameservers)
- A **Cloudflare account** with that domain added as a zone
- A **Raspberry Pi 4** running Ubuntu 22.04 ARM64 with a public IP address
- **Psiphon Conduit binary** — see Step 1a below

> **Not using Cloudflare?** The installer requires a Cloudflare Origin
> Certificate to validate TLS setup. If you want to use a different TLS
> provider (e.g. Let's Encrypt), see
> [`docs/tls-setup.md`](tls-setup.md) — you will configure nginx manually and
> skip the installer's TLS prompts.

---

## Step 1a — Psiphon Conduit binary

`install.sh` installs Psiphon Conduit automatically. You have three options:

### Option A — Let the installer download it (recommended for most users)

During Phase 1x of the install, you will be asked:

```
Download conduit v2.0.0 from GitHub now? [y/N]:
```

Answer `y` and the installer will download the binary, verify its SHA-256
checksum against the official `checksums.txt`, run `--version` to confirm
the binary executes, and install it.

**Requires:** internet access from the Pi during install.

### Option B — Place the binary in the repository directory

If you have already downloaded the binary (e.g. from a machine with better
connectivity), copy it to the same directory as `install.sh`:

```bash
# On the Pi, from the conduit-control-center directory:
cp /path/to/conduit-linux-arm64 ./conduit
chmod +x ./conduit
```

The installer will find it at `./conduit`, run `--version` to validate it,
and install it — no download required.

### Option C — Install the binary to PATH before running install.sh

If `conduit` is already in PATH (e.g. `/usr/local/bin/conduit`), the
installer will use it. The binary is still validated with `--version` before
installation.

### Which version?

The installer expects **Conduit v2.0.0**. It validates the version string
from `--version` output and will reject a binary that does not report this
version. To use a different version, update `CONDUIT_VERSION` in `install.sh`
only after testing it manually.

> **Note on Conduit releases:** Psiphon does not GPG-sign release binaries.
> The installer uses SHA-256 from the official `checksums.txt` to verify
> integrity (not authenticity) when downloading from GitHub.

---

## Step 2 — Create the DNS A record

Log in to the [Cloudflare dashboard](https://dash.cloudflare.com), select your
zone, and go to **DNS → Records**.

Click **Add record** and fill in the fields exactly as follows:

| Field | Value |
|---|---|
| **Type** | **A** |
| **Name** | The subdomain you want (e.g. `conduit`) |
| **IPv4 address** | Your Pi's current public IP (see note below) |
| **Proxy status** | **Proxied (orange cloud)** — see warning below |
| **TTL** | Auto |

### Getting your Pi's current public IP

SSH into your Pi and run:

```bash
curl https://api.ipify.org
```

Use that IP as the IPv4 address. The DDNS script (`cloudflare-ddns.sh`) will
keep this record updated automatically every 5 minutes after install, so the
exact value does not matter as long as it is a valid IPv4 address.

> **Do not use `1.1.1.1` or any other placeholder IP.** Use the real current
> IP of your Pi so the record is meaningful from the start.

### Record type must be A — not CNAME

> ⚠️ **The record type must be A. CNAME records are not supported.**
>
> The DDNS script (`scripts/cloudflare-ddns.sh`) queries for type-A records
> specifically (`?type=A&name=...`). A CNAME record with the correct name will
> pass the DNS check in the installer but will silently fail to update at
> runtime. Always create an A record.

### Proxy must be ON (orange cloud)

> ⚠️ **The Cloudflare proxy (orange cloud icon) must be enabled. This is a
> hard requirement, not a recommendation.**
>
> The TLS certificate used by this project is a **Cloudflare Origin
> Certificate**. This certificate is issued by the Cloudflare CA and is only
> trusted when the Cloudflare proxy is between the browser and your Pi. If the
> proxy is grey (DNS-only), browsers will reject the certificate with a
> `NET::ERR_CERT_AUTHORITY_INVALID` error and the dashboard will be
> unreachable.
>
> If you ever need to disable the proxy temporarily (e.g. to SSH in via the
> domain), re-enable it before trying to reach the dashboard.

After saving the record, confirm it appears in the DNS records list with an
orange cloud icon next to the name.

---

## Step 3 — Set SSL/TLS mode to Full (strict)

In the Cloudflare dashboard, go to **SSL/TLS → Overview**.

Set the encryption mode to **Full (strict)**.

| Mode | Meaning | Use this? |
|---|---|---|
| Off | HTTP only | No |
| Flexible | Cloudflare ↔ Pi is HTTP | No |
| Full | Cloudflare ↔ Pi is HTTPS, cert not verified | No |
| **Full (strict)** | Cloudflare ↔ Pi is HTTPS, cert verified | **Yes** |

> **Why Full (strict)?** The nginx configuration trusts the Cloudflare Origin
> Certificate. Full (strict) tells Cloudflare to verify the certificate
> presented by your Pi — this closes the gap between Cloudflare and your
> server that "Flexible" and plain "Full" leave open.

---

## Step 4 — Create a Cloudflare API token

The installer uses the API token to:

1. Confirm the token itself is valid
2. Look up your zone ID from the zone name
3. Check that the DNS A record exists and has proxy enabled
4. (After install) The DDNS script uses the same token to update the A record
   when your Pi's IP changes

### Create the token

Go to **My Profile → API Tokens → Create Token**.

Choose **Create Custom Token**.

| Field | Value |
|---|---|
| **Token name** | `conduit-cc` (or any name you recognise) |
| **Permissions** | `Zone` → `DNS` → **Edit** |
| | `Zone` → `Zone` → **Read** |
| **Zone Resources** | `Include` → `Specific zone` → *select your zone* |
| **IP Address Filtering** | Optional — leave blank for now |
| **TTL** | Optional — leave blank for no expiry |

> **Scope the token to the specific zone only.** Do not grant account-level
> permissions. Do not use your Global API Key.
>
> The two permissions required are:
> - `Zone:DNS:Edit` — so the DDNS script can update the A record
> - `Zone:Zone:Read` — so the installer can look up your zone ID

Click **Continue to summary**, then **Create Token**.

**Copy the token now.** Cloudflare will only show it once. Store it somewhere
safe (e.g. a password manager) until you run `install.sh` — you will paste it
at the first installer prompt.

---

## Step 5 — Obtain a Cloudflare Origin Certificate

The Origin Certificate is the TLS certificate installed on your Pi. It secures
the connection between Cloudflare's edge and your server.

See **[`docs/tls-setup.md`](tls-setup.md)** for the full certificate creation,
transfer, and verification procedure.

Come back here and complete Step 6 before running the installer.

---

## Step 6 — Confirm certificate is in place on the Pi

Before running `install.sh`, verify:

1. The certificate file exists at `/etc/conduit-cc/tls/origin.pem`
2. The private key file exists at `/etc/conduit-cc/tls/origin.key`
3. The verification commands in `tls-setup.md` pass without errors

The installer (`install.sh` Phase 1g–1h) will repeat these checks
automatically. Running them manually first saves time if something is wrong.

---

## Pre-installation checklist

Use this checklist to confirm everything is ready before running `install.sh`.
The installer will validate each item — a failure at any point stops the
install cleanly before making any changes.

```
Cloudflare account and zone
  [ ] Domain is added to Cloudflare and nameservers are active

DNS record (install.sh Phase 1f)
  [ ] Record type is A (not CNAME)
  [ ] Record name is the subdomain you want to use
  [ ] IPv4 address is the Pi's current public IP
  [ ] Proxy status is orange cloud (Proxied)

SSL/TLS mode (install.sh Phase 1e validation context)
  [ ] SSL/TLS mode is set to Full (strict)

API token (install.sh Phase 1d)
  [ ] Token has Zone:DNS:Edit permission
  [ ] Token has Zone:Zone:Read permission
  [ ] Token is scoped to the specific zone only
  [ ] Token has been copied and stored safely

TLS certificate (install.sh Phase 1g–1h)
  [ ] Origin Certificate created with key type RSA (2048)
  [ ] Certificate saved to /etc/conduit-cc/tls/origin.pem on the Pi
  [ ] Private key saved to /etc/conduit-cc/tls/origin.key on the Pi
  [ ] openssl x509 check passes (see tls-setup.md)
  [ ] openssl rsa check passes (see tls-setup.md)
  [ ] Modulus hash of cert and key match (see tls-setup.md)
```

Once all items are checked, run the installer:

```bash
sudo bash install.sh
```

---

## Post-install — firewall (UFW) rules

The CCC dashboard requires only three inbound **TCP** ports:

| Port | Purpose |
|---|---|
| 22/tcp | SSH administration |
| 80/tcp | HTTP (redirected to HTTPS) |
| 443/tcp | HTTPS dashboard (installer-selected port; 443 is the default, but may be 2053, 8443, etc.) |

`install.sh` configures UFW to allow exactly these. After installation, verify:

```bash
sudo ufw status numbered
```

You should see only `22/tcp`, `80/tcp`, and the HTTPS TCP port you selected at
install time (`443/tcp` by default, but it may be `2053/tcp`, `8443/tcp`, etc.),
plus their IPv6 equivalents. Nothing else is required for the dashboard.

### About Conduit's UDP ports

Psiphon Conduit opens dynamic, high-numbered **UDP** ports for its in-proxy
peer traffic. You can inspect them with:

```bash
ss -ulnp | grep conduit
```

These ports are chosen dynamically and change during runtime as well as
between Conduit restarts and versions. This was confirmed on a Raspberry Pi 2
field install, where the observed UDP set changed shortly after start, so
there is no fixed UDP port to open. In the validated Raspberry Pi
reference deployment, Conduit operates correctly with only TCP 22, 80, and the installer-selected HTTPS port open
in UFW. Additional inbound UDP rules are not required for that deployment.

> **Do not blindly add UDP rules.** Running `sudo ufw allow <port>/udp` for the
> ports shown by `ss` is both ineffective (the ports change) and an unnecessary
> increase in attack surface. Add an inbound UDP rule only if your specific
> deployment explicitly requires inbound UDP exposure, and only for the
> port(s) it actually needs.

### Project Owner checklist (Conduit UDP ports)

1. Leave UFW at the installer default: `22/tcp`, `80/tcp`, and the HTTPS TCP port
   you selected at install (`443/tcp` by default; may be `2053/tcp`, `8443/tcp`, etc.).
2. Do **not** add `ufw allow <port>/udp` rules for the ports shown by `ss`. They
   change at runtime, so any rule you add is stale almost immediately and only
   widens attack surface.
3. Confirm Conduit health the correct way, not via UDP rules:
   - `curl -s http://127.0.0.1:8000/api/health` returns `"status":"ok"`.
   - `curl -s http://127.0.0.1:9090/metrics | grep conduit_is_live` shows `1`.
   - connected/connecting client counts rise over time.
4. Only if your specific deployment provably needs inbound UDP (atypical) should
   you add a rule for the exact port(s) it needs, accepting that it must be
   re-checked after any runtime change, restart, or update (the ports can move
   during runtime, not only at restart). The reference deployment does not need this.

---

## Troubleshooting

**"Zone not found" during install**
: The zone name must match exactly what appears in the Cloudflare dashboard
  (e.g. `example.com`, not `www.example.com`). Do not include a subdomain.

**"DNS record not found or not proxied" during install**
: Confirm the record type is A and the proxy icon is orange in the Cloudflare
  DNS panel. DNS changes propagate quickly inside Cloudflare but allow up to
  5 minutes.

**"Certificate issuer check failed" during install**
: The installer checks that the certificate was issued by the Cloudflare CA.
  If you created the certificate with key type ECDSA, the private key check
  will also fail — re-create the certificate with key type RSA (2048). See
  [`docs/tls-setup.md`](tls-setup.md).

**"openssl rsa check failed" during install**
: The private key is not RSA. Re-create the Cloudflare Origin Certificate with
  key type **RSA (2048)** and transfer the new key to the Pi.

**"Rate limit exceeded" or Cloudflare API errors**
: Wait 60 seconds and re-run the installer. Cloudflare API rate limits are
  generous for normal use; hitting them usually indicates a script loop.
