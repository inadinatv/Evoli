# 🎬 EVOLI Premium • v5.0

Sinematik, Netflix seviyesinde premium streaming arayüzü. Otomatik katalog, gelişmiş player, favoriler ve izleme geçmişi ile tam entegre.

## ✨ Yeni Premium Özellikler (v5)

### 🎥 Sinematik Player
- **Özel kontroller**: Play/pause, 10sn ileri/geri, ses, tam ekran, sinema modu, PiP
- **İlerleme çubuğu**: Buffer gösterimi, sürükle-bırak seek, thumb önizleme
- **Hız kontrolü**: 0.5x - 2x arası oynatma hızı
- **Klavye kısayolları**: Space, ←/→, ↑/↓, M, F, T, P, N, Esc
- **Akıllı devam**: Kaldığın yerden otomatik devam, her 5sn'de kayıt
- **Otomatik sıradaki**: Video bitince sıradaki filme geçiş
- **Arkaplan blur**: Sinematik backdrop ve glassmorphism

### 🎨 Arayüz & Film Menü
- **Premium tasarım**: Outfit font, gradientler, glow efektleri, micro-animasyonlar
- **Hero bölümü**: Öne çıkan film, tek tıkla izle
- **İzlemeye devam et**: Yatay kaydırılabilir geçmiş, progress bar ile
- **Gelişmiş kartlar**: Hover'da büyütme, play ikonu, NEW/HD rozetleri, favori kalbi, izlenme ilerlemesi
- **Akıllı kategori menüsü**: Emoji ikonlar, sayılar, sekmeler (Tümü/Favori/Yeni), canlı arama
- **Sıralama**: En yeni, en eski, A-Z, Z-A, karışık
- **Favoriler**: LocalStorage, badge sayacı, dışa aktarma
- **Arama**: Debounce, kategori + başlık, vurgulu chip'ler, temizle butonu
- **Responsive**: Mobil swipe, bottom sheet player, dokunmatik uyumlu

### 🚀 Sunucu İyileştirmeleri
- `films.json` artık direkt servis ediliyor (eski sürümde 404 veriyordu)
- CORS desteği, OPTIONS/HEAD
- Yeni endpointler: `/api/categories`, `/api/stats`, `/api/films?full=1`
- Güvenli statik dosya servisi, MIME ve cache kontrol
- Poster için 1 gün cache, video için range desteği

## Kurulum

```bash
git clone https://github.com/KULLANICI/film-bot.git
cd film-bot
pip install -r requirements.txt --break-system-packages
```

## Kullanım

```bash
python bot.py
```

3 seçenek:
- **1** — Manuel tarama
- **2** — Sadece HTML sunucuyu başlat (premium UI)
- **3** — **FULL OTOMASYON**: İlk tarama + sunucu + her 6 saatte otomatik tarama

Direkt sunucu:
```bash
python server.py
```

## Erişim

- Arayüz: `http://127.0.0.1:8000`
- Aynı Wi-Fi'daki cihazlar: `http://<IP>:8000`
- M3U: `http://127.0.0.1:8000/m3u`
- JSON: `http://127.0.0.1:8000/films.json`
- API: `http://127.0.0.1:8000/api/films` | `/api/stats` | `/api/categories`

## Klavye Kısayolları

| Tuş | İşlev |
|-----|-------|
| Space | Oynat / Duraklat |
| ← / → | 10sn geri / ileri |
| ↑ / ↓ | Ses aç / kıs |
| M | Sessize al |
| F | Tam ekran |
| T | Sinema modu |
| P | Mini oynatıcı (PiP) |
| N | Sonraki film |
| Esc | Kapat |

## Ayarlar (`config.py`)

- `SCAN_INTERVAL_HOURS` → tarama aralığı (6 saat)
- `PAGES_PER_SCAN` → ana sayfa tarama sayısı
- `MAX_PAGES_PER_CATEGORY` → kategori başı limit
- `PORT`, `USER_AGENT`, `REFERER`

## Özellikler

- ✅ Premium sinematik player (custom controls, theater, PiP, hız)
- ✅ Netflix benzeri katalog + hero + devam et satırı
- ✅ Kategori filtresi, arama, sıralama, favori, yeni sekmeleri
- ✅ İzleme geçmişi & favoriler (localStorage)
- ✅ Benzer filmler, paylaş, kopyala, rastgele, devam et
- ✅ User-Agent + Referer proxy, range desteği
- ✅ Otomatik güncelleme, çift kayıt engelleme, M3U gruplama
- ✅ Tam responsive, klavye ve dokunmatik uyumlu
