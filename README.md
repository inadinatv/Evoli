# 🎬 EVOLI Premium • v6

Netflix tarzı arayüz + kendi kendini onaran video kaynağı çözümü + tam arşiv
taraması. Bu sürümün tek odağı: **kataloğun tamamı bulunsun ve bulunan her
film oynasın.**

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
| Tek film sağlık kontrolü | `/api/health/<id>` |
| Video / afiş | `/stream/<id>` • `/poster/<id>` • HLS: `/hls/<id>/<n>` |
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
| `PORT`, `SCAN_INTERVAL_HOURS`, `REQUEST_DELAY` | 8000, 6, 0.15 | — |

## 🧪 Testler

İnternet gerektirmez: `tests/fakesite.py` gerçek sitenin davranışını taklit
eder (hotlink korumalı CDN, kaçışlanmış JW Player kaynağı, mp4'süz gömme
sayfası, ölü CDN alan adı, HLS yayını, sitemap + REST + kategori sayfaları).

```bash
python -m unittest discover -s tests -t . -v
```

33 test şunları doğrular: keşif (sitemap/REST/crawl + sayfalama düzeltmesi),
kaynak çıkarma, Referer/CDN yedekleme, aday sırası (önbellek → kayıtlı →
şablon), erişilemeyen ağda kayıtların bozuk işaretlenmemesi, devre kesici,
aralık taklidi (206), HEAD, poster, HLS yeniden yazımı, onarım, API uçları,
çift kayıt engelleme.

## 📁 Dosyalar

```
bot.py       giriş noktası (menü + CLI + zamanlayıcı)
config.py    tüm ayarlar (ortam değişkeniyle geçersiz kılınabilir)
net.py       HTTP katmanı: toleranslı TLS, tekrar deneme, başlık adayları
extract.py   HTML/XML ayrıştırma: medya, gömme, id, afiş, kategori, sitemap
resolver.py  ★ çalışan video adresini bulur (çok adaylı, önbellekli, onarım)
scraper.py   keşif + film çıkarma + doğrulama + katalog/M3U üretimi
server.py    arayüz, API ve video/afiş/HLS proxy'si
index.html   premium arayüz + dayanıklı oynatıcı
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
| Arayüz yavaş açılıyor | `films.json` gzip ile servis edilir; büyük katalogda `/api/films?limit=48` kullan |

## 🔁 Bakım / GitHub Actions

Depoda 6 saatte bir çalışan `update.yml` iş akışı var: testleri çalıştırır, tam
tarama yapar, `films.json` + `playlist.m3u` + `playlist_local.m3u` değiştiyse
commit eder. Tarama sırasında akış doğrulaması kapatılır (`EVOLI_VERIFY_STREAMS=0`):
GitHub sunucularının IP'leri çoğu zaman CDN tarafından engellenir ve bu, çalışan
kayıtları yanlışlıkla "bozuk" gösterebilir. Doğrulama kendi makinende
`python bot.py repair` ile yapılır.

> **Not:** Güncellenmiş iş akışı bu depoda `docs/ci-update.yml.txt` olarak duruyor.
> `.github/workflows/` altına yazmak GitHub App'te `workflows` izni gerektirdiği
> için o değişikliği kendin uygulaman gerekiyor:
>
> ```bash
> cp docs/ci-update.yml.txt .github/workflows/update.yml
> git add .github/workflows/update.yml
> git commit -m "ci: run tests, skip stream verification on runner IPs"
> git push
> ```
>
> (Alternatif: Arena GitHub App'ine repo ayarlarından `Workflows: Read and write`
> izni verirsen bunu ben de push edebilirim.)

## Klavye kısayolları

| Tuş | İşlev |
|-----|-------|
| Space | Oynat / Duraklat |
| ← / → | 10sn geri / ileri |
| ↑ / ↓ | Ses aç / kıs |
| M / F / T / P / N | Sessiz / Tam ekran / Sinema / PiP / Sonraki |
| Esc | Kapat |
