# ProxyScrape Multi-Worker Harvester (Producer-Consumer + OTP)

Paket otomasi lengkap pendaftaran akun ProxyScrape, verifikasi OTP email via Cloudflare Email Worker, aktivasi Premium Trial 7 Hari, dan ekstraksi proxy list terautentikasi (`http://user:pass@ip:port`).

---

## 📁 Struktur File

- `auto_proxyscrape.py` : Script utama harvester Python (Producer Camoufox + Consumer Verifikasi).
- `cloudflare_email_worker.js` : Worker Cloudflare untuk Email Routing (menangkap OTP email dan meneruskan ke receiver).
- `mail_receiver.js` : Server ringan Node.js untuk menampung webhook email & melayani polling OTP.
- `.env.example` : Template konfigurasi environment.
- `requirements.txt` : Kebutuhan library Python.
- `output/` : Direktori penyimpanan akun (`proxyscrape_accounts.json`) dan list proxy (`proxies_http.txt`).

---

## 🛠️ Langkah Setup & Penggunaan

### 1. Setup Email Receiver (Cloudflare Worker)
Agar pendaftaran bisa menggunakan domain sendiri:
1. Pastikan domain sudah aktif di Cloudflare.
2. Buka **Cloudflare Dashboard -> Workers & Pages -> Create Worker**.
3. Paste isi script `cloudflare_email_worker.js` ke editor worker.
4. Di bagian **Settings -> Variables**, tambahkan Environment Variable:
   - Variable Name: `WEBHOOK_URL`
   - Value: `http://IP_VPS_KAMU:3333/api/incoming-email`
5. Buka **Email Routing -> Routing Rules -> Catch-all rule**:
   - Action: Pilih **Send to a Worker**
   - Destination: Pilih worker yang baru dibuat tadi.

### 2. Jalankan Mail Receiver Lokal
Jalankan server receiver di VPS kamu (pastikan port 3333 terbuka):
```bash
node mail_receiver.js
```
*(Bisa dijalankan di background dengan `pm2 start mail_receiver.js --name mail-receiver`)*

### 3. Install Dependencies Python
```bash
pip install -r requirements.txt
```

### 4. Konfigurasi Harvester
Salin `.env.example` menjadi `.env`:
```bash
cp .env.example .env
```
Sesuaikan pengaturannya:
```ini
MAIL_DOMAIN=domainkamu.com
MAIL_OTP_BASE=http://localhost:3333
WARP_PROXY=socks5://127.0.0.1:1080
```

### 5. Jalankan Harvester
- **Panen 10 akun (default):**
  ```bash
  python auto_proxyscrape.py --count 10
  ```
- **Kustom domain & worker via CLI:**
  ```bash
  python auto_proxyscrape.py --count 50 --workers 3 --consumers 4 --domain domainkamu.com
  ```
- **Koneksi direct tanpa Cloudflare WARP:**
  ```bash
  python auto_proxyscrape.py --count 20 --no-warp
  ```

---

## 📊 Format Output

1. **`output/proxyscrape_accounts.json`**:
   Detail lengkap tiap akun dalam format newline-delimited JSON:
   - Email & Password
   - Access Token
   - Account ID
   - Proxy Username & Proxy Password
   - Daftar 100 proxy hasil tangkapan

2. **`output/proxies_http.txt`**:
   Kumpulan proxy terautentikasi siap pakai:
   ```text
   http://username:password@45.3.33.32:3129
   http://username:password@209.50.185.157:3129
   ```
