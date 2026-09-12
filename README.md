# 🎬 FilmBot v4.0

Otomatik güncellenen katalog sistemi. Siteye film eklendikçe JSON'ı günceller,
HTML arayüzünde ve IPTV listesinde gösterir.

## Kurulum

```bash
git clone https://github.com/KULLANICI/film-bot.git
cd film-bot
pip install -r requirements.txt
```

## Kullanım

```bash
python bot.py
```

3 seçenek çıkar:
- **1** — Manuel tarama
- **2** — Sadece HTML sunucuyu başlat
- **3** — **FULL OTOMASYON**: İlk tarama + sunucu + her 6 saatte bir otomatik tarama

## Ayarlar

`config.py` dosyasından:
- `SCAN_INTERVAL_HOURS` → otomatik tarama aralığı (varsayılan 6 saat)
- `PAGES_PER_SCAN` → her taramada kaç sayfa gez
- `PORT` → sunucu portu
- `USER_AGENT`, `REFERER` → header bilgileri

## Erişim

- Arayüz: `http://127.0.0.1:8000`
- Aynı Wi-Fi'daki diğer cihazlardan: `http://<TELEFON_IP>:8000`
- M3U listesi: `http://127.0.0.1:8000/m3u`
- JSON API: `http://127.0.0.1:8000/api/films`

## Özellikler

- ✅ Netflix benzeri katalog arayüzü (kapak resimli kartlar)
- ✅ Kategori filtresi ve arama
- ✅ User-Agent + Referer destekli video proxy (sorunsuz oynatma)
- ✅ Otomatik güncelleme (scheduler)
- ✅ Çift kayıt engelleme (JSON ID bazlı)
- ✅ M3U gruplama (`group-title` ile IPTV kategorisi)
