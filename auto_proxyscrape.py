import os

def _load_dotenv(path=".env"):
    """Baca .env (KEY=VALUE) tanpa dependensi eksternal."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), path)) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_dotenv()
import sys
import time
import json
import secrets
import string
import argparse
import subprocess
import asyncio
import threading
import requests
from camoufox.async_api import AsyncCamoufox
from tempik_mail import TempikMail

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DATA_FILE = os.path.join(OUTPUT_DIR, "proxyscrape_accounts.json")
PROXIES_FILE = os.path.join(OUTPUT_DIR, "proxies_http.txt")
ENV_FILE = os.path.join(BASE_DIR, ".env")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_env():
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith(chr(35)) and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v

load_env()

MAIL_DOMAIN = os.getenv("MAIL_DOMAIN", "example.com")
MAIL_OTP_BASE = os.getenv("MAIL_OTP_BASE", "http://localhost:3333")
MAIL_BASE = os.getenv("MAIL_BASE", "https://mail.example.com")

# Turnstile needs a real WebGL context. Camoufox headless on this VPS reports
# "WebGL creation failed", so the harvester runs headful on Xvfb (:99) with
# software GL forced on; these prefs make the renderer look plausible.
FIREFOX_PREFS = {
    "webgl.force-enabled": True,
    "webgl.disabled": False,
    "webgl.disable-angle": False,
    "layers.acceleration.force-enabled": True,
    "gfx.webrender.all": True,
}

# Tempik adapter replaces the pack's Cloudflare Email Worker + Node receiver.
MAIL = TempikMail(base=MAIL_BASE, domain=MAIL_DOMAIN)
TARGET_BASE = "https://dashboard.proxyscrape.com"
API_BASE = "https://api.proxyscrape.com/v4"
TURNSTILE_SITEKEY = os.getenv("TURNSTILE_SITEKEY", "0x4AAAAAAAFWUVCKyusT9T8r")

WARP_PROXY = os.getenv("WARP_PROXY", "socks5://127.0.0.1:1080")
WARP_REQUESTS_PROXIES = {
    "http": f"socks5h://{WARP_PROXY.split('://')[-1]}",
    "https": f"socks5h://{WARP_PROXY.split('://')[-1]}"
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

HTML_MOCK = f"""<!DOCTYPE html>
<html>
<head><script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script></head>
<body><div class="cf-turnstile" data-sitekey="{TURNSTILE_SITEKEY}"></div></body>
</html>"""

file_lock = threading.Lock()
tg_lock = threading.Lock()
warp_lock = threading.Lock()

def log(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{ts} {msg}", flush=True)

def gen_password():
    chars = string.ascii_letters + string.digits + "!@$%^&*"
    return "".join(secrets.choice(chars) for _ in range(12)) + "A1!"

def rotate_warp():
    with warp_lock:
        try:
            subprocess.run(["warp-cli", "--accept-tos", "tunnel", "rotate-keys"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            time.sleep(1.0)
        except Exception:
            pass

def send_telegram_alert(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    with tg_lock:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if TELEGRAM_THREAD_ID:
            try:
                payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
            except Exception:
                pass
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=8
            )
        except Exception as e:
            log(f"[-] Telegram error: {e}")

async def poll_otp(email, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        def fetch():
            try:
                r = requests.get(f"{MAIL_OTP_BASE}/api/wait-otp?to={email}&timeout=5", timeout=7)
                d = r.json()
                if d.get("status") == "found" and d.get("otp"):
                    return str(d["otp"])
            except Exception:
                pass
            try:
                r2 = requests.get(f"{MAIL_OTP_BASE}/api/message?to={email}", timeout=3)
                d2 = r2.json()
                if d2.get("status") == "found" and d2.get("message"):
                    snippet = str(d2["message"].get("snippet", "")) + " " + str(d2["message"].get("subject", ""))
                    import re
                    m = re.search(r"verification\s*code:\s*([a-f0-9]{10})\b", snippet, re.I)
                    if m:
                        return m.group(1)
            except Exception:
                pass
            return None

        res = await asyncio.to_thread(fetch)
        if res:
            return res
        await asyncio.sleep(2)
    return None

async def registration_producer(wid, otp_queue, stop_event, use_warp, domain):
    mock_url = f"{TARGET_BASE}/mock-turbo-w{wid}/"
    proxy_opt = {"server": WARP_PROXY} if use_warp else None

    async with AsyncCamoufox(headless=False, geoip=True, proxy=proxy_opt,
                             firefox_user_prefs=FIREFOX_PREFS) as browser:
        page = await browser.new_page()
        await page.route(
            mock_url,
            lambda route: route.fulfill(body=HTML_MOCK, status=200, headers={"content-type": "text/html"})
        )

        while not stop_event.is_set():
            try:
                await page.goto(mock_url)
            except Exception:
                await asyncio.sleep(1)
                continue

            token = None
            start_t = time.time()
            while time.time() - start_t < 25:
                try:
                    val = await page.input_value("[name=cf-turnstile-response]", timeout=500)
                    if val and len(val) > 20:
                        token = val
                        break
                    try:
                        await page.locator("//div[@class='cf-turnstile']").click(timeout=300)
                    except Exception:
                        pass
                except Exception:
                    pass
                await asyncio.sleep(0.4)

            if not token:
                log(f"[W{wid}] [-] Turnstile timeout, retrying...")
                if use_warp:
                    rotate_warp()
                await asyncio.sleep(1)
                continue

            email = MAIL.new_address()
            password = gen_password()

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Origin": TARGET_BASE,
                "Referer": f"{TARGET_BASE}/sign-up"
            }
            proxies = WARP_REQUESTS_PROXIES if use_warp else None

            def do_reg():
                try:
                    r = requests.post(
                        f"{API_BASE}/account/auth/register",
                        data={"email": email, "password": password, "cf_turnstile_token": token},
                        headers=headers,
                        proxies=proxies,
                        timeout=15
                    )
                    tok = r.json().get("access_token")
                    if tok:
                        return tok
                except Exception:
                    pass
                return None

            access_token = await asyncio.to_thread(do_reg)
            if not access_token:
                log(f"[W{wid}] [-] Registration failed, retrying...")
                if use_warp:
                    rotate_warp()
                await asyncio.sleep(1)
                continue

            auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "User-Agent": headers["User-Agent"]
            }

            def trigger_reset():
                try:
                    requests.post(
                        f"{API_BASE}/account/reset-verification-code",
                        headers=auth_headers,
                        proxies=proxies,
                        timeout=10
                    )
                except Exception:
                    pass

            await asyncio.to_thread(trigger_reset)

            item = {
                "email": email,
                "password": password,
                "access_token": access_token,
                "auth_headers": auth_headers,
                "use_warp": use_warp
            }
            await otp_queue.put(item)
            log(f"[W{wid}] [1/5] Registered {email} -> antre verifikasi OTP...")
            await asyncio.sleep(0.5)

async def otp_consumer(cid, otp_queue, stop_event, target_count, state, notify_every):
    while not stop_event.is_set():
        try:
            item = await asyncio.wait_for(otp_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue

        email = item["email"]
        password = item["password"]
        access_token = item["access_token"]
        auth_headers = item["auth_headers"]
        use_warp = item["use_warp"]
        proxies = WARP_REQUESTS_PROXIES if use_warp else None

        log(f"[C{cid}] [2/5] Menunggu OTP email untuk {email}...")
        otp = await asyncio.to_thread(MAIL.wait_otp, email, 150)
        if not otp:
            log(f"[C{cid}] [-] OTP timeout untuk {email}, dilewati.")
            otp_queue.task_done()
            continue

        log(f"[C{cid}] [3/5] OTP {otp} diterima! Memverifikasi email...")

        def finalize():
            try:
                requests.post(
                    f"{API_BASE}/account/verify-email",
                    json={"verificationCode": otp},
                    headers=auth_headers,
                    proxies=proxies,
                    timeout=10
                )
            except Exception:
                pass

            account_id = None
            try:
                claim_res = requests.post(f"{API_BASE}/account/premium/claim-trial", headers=auth_headers, proxies=proxies, timeout=10)
                account_id = claim_res.json().get("account_id")
            except Exception:
                pass

            if not account_id:
                try:
                    me_res = requests.post(f"{API_BASE}/account/auth/me", headers=auth_headers, proxies=proxies, timeout=10).json()
                    subs = me_res.get("associatedSubaccounts", [])
                    if subs:
                        account_id = subs[0].get("AccountID")
                except Exception:
                    pass

            if not account_id:
                return None, None, None, []

            proxy_user = ""
            proxy_pass = ""

            try:
                ov_res = requests.get(f"{API_BASE}/account/{account_id}/datacenter_shared/overview", headers=auth_headers, proxies=proxies, timeout=10)
                ov_data = ov_res.json().get("data", {})
                ds = ov_data.get("services", {}).get("datacenter_shared", {})
                proxy_user = ds.get("proxy_username", "")
                proxy_pass = ds.get("proxy_password", "")
            except Exception:
                pass

            raw_proxies = []
            try:
                pl_res = requests.get(
                    f"{API_BASE}/account/{account_id}/datacenter_shared/proxy-list?type=getproxies&proxyformat=userpass&protocol=http",
                    headers=auth_headers,
                    proxies=proxies,
                    timeout=12
                )
                if proxy_user and proxy_pass:
                    raw_proxies = [f"http://{proxy_user}:{proxy_pass}@{line.strip()}" for line in pl_res.text.strip().splitlines() if line.strip() and ":" in line]
                else:
                    raw_proxies = [line.strip() for line in pl_res.text.strip().splitlines() if line.strip() and ":" in line]
            except Exception:
                pass

            return account_id, proxy_user, proxy_pass, raw_proxies

        account_id, proxy_user, proxy_pass, raw_proxies = await asyncio.to_thread(finalize)
        otp_queue.task_done()

        if not account_id:
            log(f"[C{cid}] [-] Gagal aktivasi trial untuk {email}")
            continue

        record = {
            "email": email,
            "password": password,
            "access_token": access_token,
            "account_id": account_id,
            "proxy_username": proxy_user,
            "proxy_password": proxy_pass,
            "proxy_count": len(raw_proxies),
            "proxies": raw_proxies,
            "dashboard_url": f"https://dashboard.proxyscrape.com/v2/services/premium/proxy-list/{account_id}",
            "egress": "warp" if use_warp else "direct",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        with file_lock:
            state["completed"] += 1
            cur = state["completed"]

            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            if raw_proxies:
                with open(PROXIES_FILE, "a", encoding="utf-8") as f:
                    for prx in raw_proxies:
                        f.write(f"{prx}\n")

            total_all = 0
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    total_all = len(f.readlines())

        log(f"[C{cid}] [5/5] ✅ Akun: {email} | Proxies: {len(raw_proxies)} (Batch: No.{cur}/{target_count or '∞'}, Total Store: {total_all})")

        if cur == 1 or cur % notify_every == 0 or (target_count > 0 and cur == target_count):
            text = (
                f"⚡ <b>[PROXY SCRAPE HARVESTER No.{cur}/{target_count or '∞'}]</b> Panen Berhasil! 🤑🔥\n\n"
                f"📧 <b>Email:</b> <code>{email}</code>\n"
                f"🆔 <b>Account ID:</b> <code>{account_id}</code>\n"
                f"👤 <b>Auth:</b> <code>{proxy_user}:{proxy_pass}</code>\n"
                f"🌐 <b>Proxies Captured:</b> <code>{len(raw_proxies)} Proxies</code>\n"
                f"🔗 <b>Dashboard Link:</b>\n<code>https://dashboard.proxyscrape.com/v2/services/premium/proxy-list/{account_id}</code>\n\n"
                f"📦 <b>Total Akun Terkumpul:</b> <code>{total_all}</code> Akun Siap Gas 🥱😏"
            )
            send_telegram_alert(text)

        if target_count > 0 and cur >= target_count:
            stop_event.set()
            break

async def main():
    parser = argparse.ArgumentParser(description="ProxyScrape Harvester Multi-Worker with OTP Support")
    parser.add_argument("--count", type=int, default=10, help="Jumlah akun yang ingin dipanen (0 = unlimited)")
    parser.add_argument("--workers", type=int, default=3, help="Jumlah worker browser registrasi paralel")
    parser.add_argument("--consumers", type=int, default=4, help="Jumlah consumer verifikasi OTP paralel")
    parser.add_argument("--notify-every", type=int, default=1, help="Kirim notifikasi Telegram tiap N akun")
    parser.add_argument("--domain", type=str, default=MAIL_DOMAIN, help="Domain email kustom untuk registrasi")
    parser.add_argument("--otp-url", type=str, default=MAIL_OTP_BASE, help="Base URL receiver OTP (default: http://localhost:3333)")
    parser.add_argument("--no-warp", action="store_true", help="Gunakan koneksi direct VPS tanpa Cloudflare WARP")
    parser.add_argument("--proxy", type=str, default=os.getenv("PROXY", ""),
                        help="Proxy eksplisit (mis. socks5://127.0.0.1:10801 untuk rot_bridge)")

    args = parser.parse_args()

    global WARP_PROXY, WARP_REQUESTS_PROXIES
    use_warp = not args.no_warp
    if args.proxy:
        WARP_PROXY = args.proxy
        WARP_REQUESTS_PROXIES = {"http": args.proxy, "https": args.proxy}
        use_warp = True
        log(f"[*] Override proxy: {args.proxy}")
    domain = args.domain
    # OTP now comes from the Tempik adapter (MAIL), so the old receiver URL is
    # unused; --otp-url is kept only for backwards compatibility.

    log(f"[*] ProxyScrape Harvester Mulai!")
    log(f"[*] Target: {args.count or 'Unlimited'} akun | Producers: {args.workers} | Consumers: {args.consumers}")
    log(f"[*] Domain: {domain} | OTP Receiver: {MAIL_OTP_BASE} | Mode: {'WARP' if use_warp else 'Direct'}")

    state = {"completed": 0}
    stop_event = asyncio.Event()
    otp_queue = asyncio.Queue(maxsize=100)

    producers = [
        asyncio.create_task(registration_producer(i + 1, otp_queue, stop_event, use_warp, domain))
        for i in range(args.workers)
    ]
    consumers = [
        asyncio.create_task(otp_consumer(i + 1, otp_queue, stop_event, args.count, state, args.notify_every))
        for i in range(args.consumers)
    ]

    while not stop_event.is_set():
        if args.count > 0 and state["completed"] >= args.count:
            stop_event.set()
            break
        await asyncio.sleep(1)

    for p in producers:
        p.cancel()
    for c in consumers:
        c.cancel()

    log(f"✅ HARVEST SELESAI TOTAL: {state['completed']} akun!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Dihentikan manual oleh user.")
