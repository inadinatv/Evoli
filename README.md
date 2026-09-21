# 🎬 EVOLI Premium • v6.1

Netflix tarzı arayüz + kendi kendini onaran video kaynağı çözümü + tam arşiv
taraması + **CORS/proxy engellerini aşan katman**. Tek odağı: **kataloğun
tamamı bulunsun, kapaklar gerçek olsun ve bulunan her film sorunsuz oynasın.**

---

## 🆚 v6.1'de eklenen: CORS / proxy engellerini aşma katmanı

| Katman | Ne yapar | Nerede |
|--------|----------|--------|
| **Genel vekil `/proxy?url=…&ref=…`** | Herhangi bir uzak kaynağı (video, görsel, JSON, JS) sunucumuz üzerinden akıtır: CORS, hotlink koruması, karışık içerik engelleri tarayıcıda hiç oluşmaz | `server.py` — SSRF korumalı (özel ağ hedefleri varsayılan kapalı, `EVOLI_PROXY_ALLOW_PRIVATE=1` ile açılır) |
| **Aynı origin `/hls.js`** | Oynatıcının uzak CDN'e (jsdelivr vb.) bağımlılığı bitti; hls.js önce kendi origin'imizden servis edilir, olmazsa 3 CDN denenir | `index.html` + `server.py` |
| **Çıkış proxy'si** | Evoli'nin kendisi bir ISP/ülke/DNS engeline takılıysa: `EVOLI_UPSTREAM_PROXY="socks5://127.0.0.1:1080"` (veya `http://…`) ile tüm dış istekler vekilden çıkar | `config.py` → `net.py` |
| **CORS başlıkları** | Tüm yanıtlarda `Access-Control-Allow-Origin: *`, preflight yankılama, `Cross-Origin-Resource-Policy: cross-origin`, `Access-Control-Allow-Private-Network` | `server.py` |
| **Oynatıcı yedekleri** | `/stream` → taze çözüm → **`/proxy` ile doğrudan video** (son çare). Kapaklarda `/poster` → doğrudan adres → `/proxy` zinciri | `index.html` |
| **`crossorigin="anonymous"` kaldırıldı** | Doğrudan CDN kaynağına düşen oynatma (fallback) artık CORS başlığı beklemez | `index.html` |

## 🖼 v6.1'de eklenen: gerçek kapaklar + doğru kategoriler

| Sorun | Eski davranış | Yeni davranış |
|-------|---------------|---------------|
| **Logo sahte afiş** | Sitenin `og:image`'i çoğu zaman `logo1.png` → 1114 filmde aynı logo afişti | Logo/favicon/yer tutucu görseller afiş adayı reddedilir; gerçek afiş (uploads / oynatıcı `image:` / CDN `img/{id}.jpg`) doğrulanarak kaydedilir |
| **Kategori çorbası** | Menüdeki 52 kategori film sayfasında da olduğu için HER filme 52 kategori yazılıyor, filtre anlamsızdı | Film kaydı `scope="content"` ile yalnızca içeriğe ait kategorilerden (nav/yan sütun atlanır, `rel="category"` öncelikli) doldurulur |
| **Toplu onarım** | — | `python bot.py covers` → REST ile kategoriler + doğrulanmış afişler tek geçişte düzeltilir; arayüzden 🛠 ya da `/api/repair?posters=1` |

---

## ❗ v6'da düzeltilen kök nedenler

| # | Sorun | Eski davranış | Yeni davranış |
|---|-------|---------------|---------------|
| 1 | **Bayat Referer** | `config.REFERER` içine sabit yazılmış alan adı CDN'den 403 yiyordu → video hiç açılmıyordu | Her film için *aday Referer listesi*: gömme sayfası → film sayfası → öğrenilen site adresi → config → referer'sız. İlk çalışan kullanılır ve kaydedilir |
| 2 | **Dönen (rotating) CDN alan adı** | `films.json`'a yazılan `https://cdn…/<id>.mp4` adresi alan adı değişince ölüyordu | Alan adı tarama sırasında öğrenilir (`meta.cdn_host`), tüm eski kayıtlar otomatik yeni alan adına taşınır (`retarget_streams`), eski adres `alt_streams` içinde yedek kalır |
| 3 | **Tek deneme, yedek yok** | Kaynak 403/404 verirse oynatıcı sadece "video yüklenemedi" diyordu | Sunucu sırayla dener: önbellek → kayıtlı adres (farklı Referer'larla) → CDN şablonları → **film sayfasından canlı yeniden çözüm**. Oyuncu da 3 kez yeniden dener |
| 4 | **Kategori sayfaları taranmıyordu** | `cat_url.rstrip('/') + 'page/2/'` → `…-opage/2/` (eksik `/`) → kategorilerin 2. sayfasından sonrası hiç bulunamıyordu | Doğru `/page/N/` kurulumu + **sitemap** ve **WordPress REST API** keşfi (tüm arşiv, 2016'dan bugüne) |
| 5 | **JSON'daki filmlerin hepsi görünmüyordu** | Sadece HTML crawl; tek bir regex (`/pornolar/<id>.html` + `https://…mp4`) | Kaçışlanmış URL'ler (`https:\/\/…`), `file:`/`sources:`/`og:video`/`<source>`/`data-src`, `.m3u8`/`.webm`, iç içe iframe'ler, çoklu id kalıbı |
| 6 | **Kararsız kimlik** | `str(abs(hash(src)))` — Python her çalıştırmada farklı hash üretir → çift/bozuk kayıtlar | Gömme numarası, yoksa `blake2b` tabanlı deterministik kimlik |
| 7 | **Aralık (Range) hataları** | `Accept-Ranges: bytes` her zaman gönderiliyordu, Content-Length yoksa tarayıcı yanıtın bittiğini anlayamıyordu, HEAD isteğine gövde yazılıyordu | Range gerçekten destekleniyorsa bildiriliyor; desteklenmiyorsa sunucu 206 + `Content-Range` üretiyor (ileri/geri sarma çalışır), uzunluk bilinmiyorsa `Connection: close`, HEAD'de gövde yok, `Connection: close` yankılanıyor |
| 8 | **Her istekte 790 KB JSON okuma** | `reload()` her istekte dosyayı baştan okuyordu → oynatma takılıyordu | mtime önbelleği + id/slug indeksi, metin yanıtlarında gzip |
| 9 | **m3u8 oynatılamıyordu** | HLS kaynak bulunsa bile oynatılamıyordu | Sunucu playlist'i kendi origin'ine yeniden yazar (`/hls/<id>/<n>`), tarayıcıda hls.js devreye girer |

---

## 🚀 Kurulum

```bash
git clone https://github.com/inadinatv/Evoli.git
cd Evoli
pip install -r requirements.txt        # sadece requests + urllib3
```

> Eski sürümden geliyorsan bir kez çalıştır:
> ```bash
> python bot.py migrate     # films.json'u yeni şemaya taşır (internet gerekmez)
> ```

## ▶ Kullanım

```bash
python bot.py            # interaktif menü
python bot.py server     # sadece sunucu (arayüz + video proxy)
python bot.py scan       # hızlı güncelleme: yeni filmler
python bot.py full       # tam arşiv: sitemap'teki tüm filmler
python bot.py repair     # oynamayan kaynakları yeniden çöz
python bot.py covers     # logo afişleri + yanlış kategorileri düzelt
python bot.py status     # katalog özeti
python bot.py auto       # tara + sunucu + 6 saatte bir otomatik tarama
```

Menü:

```
1) Hızlı Güncelleme      — yeni filmleri ekle (sitemap/REST)
2) Tam Arşiv Taraması    — sitedeki tüm filmler (uzun sürer)
3) Sunucuyu Başlat       — premium arayüz + video proxy
4) Full Otomasyon        — tara + sunucu + zamanlayıcı
5) Kaynakları Onar       — oynamayan videoları yeniden çöz
6) Katalog Durumu        — özet + CDN/site bilgisi
7) Kataloğu Taşı         — eski films.json'u yeni şemaya çevir
8) Kapak & Kategori Onar — logo afişleri/yanlış kategorileri düzelt
```

## 🔗 Erişim

| Ne | Adres |
|----|-------|
| Arayüz | `http://127.0.0.1:8000` |
| Aynı Wi-Fi'daki cihazlar | `http://<IP>:8000` |
| Katalog | `/films.json` • `/api/films` • `/api/films?full=1` |
| Arama/filtre (sunucu tarafı) | `/api/films?q=anne&cat=HD&limit=48&offset=0` |
| Özet | `/api/stats` • `/api/status` • `/api/meta` |
| Kaynak çözme | `/api/resolve/<id>?refresh=1&debug=1` |
| Toplu onarım | `/api/repair?limit=200` • durum: `/api/repair/status` |
| Kapak/kategori onarımı | `/api/repair?posters=1` (ya da `?kind=covers`) |
| Tek film sağlık kontrolü | `/api/health/<id>` |
| Video / afiş | `/stream/<id>` • `/poster/<id>` • HLS: `/hls/<id>/<n>` |
| **Genel vekil (CORS kırıcı)** | `/proxy?url=<encoded>&ref=<encoded>` — herhangi bir kaynak |
| **hls.js (aynı origin)** | `/hls.js` — uzak CDN engelli olsa bile HLS çalışır |
| M3U (harici oynatıcı) | `/m3u` — UA + Referer gömülü |
| M3U (proxy, en garantili) | `/playlist_local.m3u` — VLC/TV'de bunu kullan |

## 🧠 Kaynak çözme nasıl çalışıyor?

```
film (id, url, embed, stream)
   │
   ├─ 1. media_cache.json  → daha önce çalıştığı doğrulanmış adres
   ├─ 2. films.json stream → farklı Referer adaylarıyla (embed → sayfa → site → config → boş)
   ├─ 3. CDN şablonları    → öğrenilen alan adlarıyla https://{host}/{id}.mp4
   └─ 4. canlı çözüm       → film sayfası → iframe/gömme → oynatıcı kaynağı (file:/sources/og:video/m3u8)
                              ↓
                     ilk çalışan yanıt (200/206, video içerik tipi)
                              ↓
                  media_cache.json'a yazılır → sonraki istekler tek atışta
```

Oynatıcıda bir şey ters giderse ekranda **teşhis paneli** açılır: hangi adayın
hangi alan adı + Referer ile hangi HTTP durumunu döndürdüğünü gösterir
(403 = Referer/hotlink, 404 = dosya yok, 0 = ağ erişilemedi).

## ⚙️ Ayarlar (`config.py`)

Tüm ayarlar `EVOLI_<ADI>` ortam değişkeniyle geçersiz kılınabilir
(ör. `EVOLI_PORT=9000 python bot.py server`).

| Ayar | Varsayılan | Açıklama |
|------|-----------|----------|
| `BASE_URL` / `SITE_HOME` | ayna / kanonik adres | Tarama giriş noktası ve film adreslerinin alan adı |
| `MIRROR_HOSTS` | 3 ayna | Sayfa açılamazsa sırayla denenen aynalar |
| `CDN_HOSTS` | `cdn.evolliecdnsx.com` | Başlangıç CDN tohumu; yenileri otomatik öğrenilir |
| `CDN_VIDEO_TEMPLATES` | `{host}/{id}.mp4` … | Video adresi şablonları |
| `DISCOVERY_MODE` | `auto` | `auto` / `sitemap` / `rest` / `crawl` |
| `MAX_FILMS_PER_SCAN` | 300 | Hızlı taramada eklenecek yeni film sayısı (0 = sınırsız) |
| `SCAN_WORKERS` | 8 | Eşzamanlı çözme işçisi |
| `VERIFY_STREAMS` / `VERIFY_SAMPLE` | `True` / 60 | Taramada kaç kaynak doğrulansın |
| `EMULATE_RANGE` | `True` | Upstream Range desteklemiyorsa sunucu taklit etsin |
| `CACHE_TTL_OK` / `CACHE_TTL_FAIL` | 12 saat / 10 dk | Çözüm önbelleği geçerlilik süreleri |
| `UPSTREAM_PROXY` | boş | Çıkış proxy'si (`http://…` / `https://…` / `socks5://…`; socks için `pip install PySocks`) |
| `PROXY_ALLOW_PRIVATE` | `False` | `/proxy` ile özel/yerel ağ hedefleri vekletilsin mi (SSRF) |
| `HLSJS_SOURCES` | 3 CDN | `/hls.js` sunucusunun indirme yedekleri |
| `PORT`, `SCAN_INTERVAL_HOURS`, `REQUEST_DELAY` | 8000, 6, 0.15 | — |

## 🧪 Testler

İnternet gerektirmez: `tests/fakesite.py` gerçek sitenin davranışını taklit
eder (hotlink korumalı CDN, kaçışlanmış JW Player kaynağı, mp4'süz gömme
sayfası, ölü CDN alan adı, HLS yayını, sitemap + REST + kategori sayfaları).

```bash
python -m unittest discover -s tests -t . -v
```

43 test şunları doğrular: keşif (sitemap/REST/crawl + sayfalama düzeltmesi),
kaynak çıkarma, Referer/CDN yedekleme, aday sırası (önbellek → kayıtlı →
şablon), erişilemeyen ağda kayıtların bozuk işaretlenmemesi, devre kesici,
aralık taklidi (206), HEAD, poster, HLS yeniden yazımı, onarım, API uçları,
çift kayıt engelleme, **logo afiş reddi, kategori çorbası düzeltmesi, `/proxy`
CORS vekili (SSRF koruması dahil), aynı origin `/hls.js`, kapak onarım işi**.

## 📁 Dosyalar

```
bot.py       giriş noktası (menü + CLI + zamanlayıcı)
config.py    tüm ayarlar (ortam değişkeniyle geçersiz kılınabilir)
net.py       HTTP katmanı: toleranslı TLS, tekrar deneme, başlık adayları, çıkış proxy'si, SSRF denetimi
extract.py   HTML/XML ayrıştırma: medya, gömme, id, afiş (logo reddi), kategori, sitemap
resolver.py  ★ çalışan video/afiş adresini bulur (çok adaylı, önbellekli, onarım)
scraper.py   keşif + film çıkarma + doğrulama + katalog/M3U üretimi + fix_metadata (kapak/kategori)
server.py    arayüz, API, /proxy CORS vekili ve video/afiş/HLS/hls.js proxy'si
index.html   premium arayüz + dayanıklı oynatıcı (çok yedekli kaynak zinciri)
tests/       sahte site + uçtan uca testler
films.json   katalog (üretilir)         media_cache.json  çözüm önbelleği (üretilir, git'e girmez)
playlist.m3u harici oynatıcı listesi    playlist_local.m3u proxy listesi
```

## 🩺 Sorun giderme

| Belirti | Çözüm |
|---------|-------|
| Hiçbir video oynamıyor | `python bot.py repair` → CDN alan adı/Referer yeniden öğrenilir. Ardından `/api/status` altında `resolver.stats` alanına bak |
| Bazı videolar oynamıyor | Oyuncudaki **🛠 Kaynakları Onar** düğmesi veya `python bot.py repair` |
| Teşhis panelinde `403` | Referer sorunu: `config.REFERER`/`meta.referer` bayat. `repair` çalışan refereri bulup `media_cache.json`'a yazar |
| Teşgis panelinde `0 / bağlanılamadı` | Ağ/VPN/DNS engeli. Kayıtlar "bozuk" işaretlenmez, ağ açılınca kendiliğinden çözülür |
| Katalog eksik görünüyor | `python bot.py full` (sitemap'ten tüm arşiv) |
| VLC/TV'de oynatmak istiyorum | `http://127.0.0.1:8000/playlist_local.m3u` (Referer/UA derdi yok) |
| Kapaklar hep aynı logo | `python bot.py covers` — gerçek afişler tek tek doğrulanıp düzeltilir |
| Kategori seçince hep aynı filmler | `python bot.py covers` — kategori çorbası REST ile temizlenir |
| Tarayıcı konsolunda CORS hatası | Kaynağı `/proxy?url=…` üzerinden iste; sunucu tüm yanıtlara CORS başlığı ekler |
| HLS oynatılmıyor + "hls.js indirilemedi" | Ağ CDN'leri engelliyor; `/hls.js` sunucu yedeklerinden indirir, `EVOLI_UPSTREAM_PROXY` ile çıkış proxy'si verebilirsin |
| Arayüz yavaş açılıyor | `films.json` gzip ile servis edilir; büyük katalogda `/api/films?limit=48` kullan |

## 🔁 Bakım / GitHub Actions

Depoda 6 saatte bir katalog güncelleyen `update.yml` iş akışı var.  **Testleri
koşturan sürümlü iş akışı** `docs/ci-update.yml.txt` içinde hazır duruyor
(taramadan önce 43 testi koşuturur, runner IP'lerinde doğrulamayı kapatarak
yanlış "bozuk" işaretlemeleri önler).  GitHub App'in `workflows` izni olmadığı
için bu dosya repodaki `.github/workflows/update.yml`'e elle kopyalanmalı:

```bash
cp docs/ci-update.yml.txt .github/workflows/update.yml
git add .github/workflows/update.yml
git commit -m "ci: run tests before catalog update"
git push
```

(Alternatif: GitHub App'e repo ayarlarından `Workflows: Read and write` izni
verirsen bu kopyalamayı ben de yapabilirim.)

Katalogda logo afişler/çorba kategoriler biriktiyse (eski sürümden kalma) bir
kez `python bot.py covers` çalıştır — REST ile kategoriler, doğrulanmış afişlerle
kapaklar tek seferde düzeltilir.

## Klavye kısayolları

| Tuş | İşlev |
|-----|-------|
| Space | Oynat / Duraklat |
| ← / → | 10sn geri / ileri |
| ↑ / ↓ | Ses aç / kıs |
| M / F / T / P / N | Sessiz / Tam ekran / Sinema / PiP / Sonraki |
| Esc | Kapat |
