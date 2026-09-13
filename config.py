# -*- coding: utf-8 -*-
"""Evoli Film Bot — merkezi ayarlar.

Tüm modüller ``import config`` üzerinden buraya bakar; böylece testler ve
ortamlar değerleri tek yerden değiştirebilir.  Ortam değişkenleri
(``EVOLI_*``) config değerlerinin üzerine yazılır.
"""
import os

# --------------------------------------------------------------------------- #
# Site
# --------------------------------------------------------------------------- #
# Taramaya buradan başlanır.  Site dönüşümlü (rotating) aynalar kullanıyor,
# bu yüzden kanonik adres ayrıca tutulur ve çalışma sırasında öğrenilir.
BASE_URL = "https://1ppa99.evooli.com"
SITE_HOME = "https://www.evooli.com"
SITENAME = "Evooli"

# Bilinen aynalar — istek başarısız olursa sırayla denenir.
MIRROR_HOSTS = [
    "www.evooli.com",
    "1ppa99.evooli.com",
    "evooli.com",
]

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15; 2412DPC0AG Build/AP3A.240905.015.A2; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
    "Mobile Safari/537.36"
)
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
# Eski (miras) referer.  CDN bunu artık kabul etmiyor olabilir, bu yüzden
# sadece adaylardan biri olarak denenir — tek doğru kabul edilmez.
REFERER = "https://www.evoolipxnyxzq.shop/"

UA_CANDIDATES = [USER_AGENT, DESKTOP_USER_AGENT]

# --------------------------------------------------------------------------- #
# Sunucu
# --------------------------------------------------------------------------- #
HOST = "0.0.0.0"
PORT = 8000
GZIP_MIN_BYTES = 1024          # bu boyuttan büyük metin yanıtları gzip'lenir
EMULATE_RANGE = True           # upstream Range desteklemiyorsa sunucu taklit eder
PROXY_CHUNK = 131072
MAX_PROXY_CANDIDATES = 14      # tek bir /stream isteğinde denenecek kaynak sayısı
MAX_RESOLVE_CANDIDATES = 14    # doğrulama/onarım sırasında denenecek kaynak sayısı

# --------------------------------------------------------------------------- #
# Tarama / keşif
# --------------------------------------------------------------------------- #
SCAN_INTERVAL_HOURS = 6
PAGES_PER_SCAN = 5             # HTML taramasında ana sayfadan kaç sayfa
SCAN_ALL_CATEGORIES = True
MAX_PAGES_PER_CATEGORY = 10
REQUEST_DELAY = 0.15           # istekler arası bekleme (işçi başına)
SCAN_WORKERS = 8               # eşzamanlı film çözme işçisi
MAX_FILMS_PER_SCAN = 300       # 0 = sınırsız (tam arşiv için bot menüsünü kullan)
FULL_ARCHIVE_LIMIT = 0         # 0 = sınırsız

# Keşif kaynakları: "auto" hepsini dener (sitemap -> REST -> HTML crawl)
DISCOVERY_MODE = "auto"
SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap-index.xml"]
SITEMAP_MAX_FILES = 400
REST_API_PATH = "/wp-json/wp/v2/posts"
REST_CATEGORIES_PATH = "/wp-json/wp/v2/categories"
REST_PER_PAGE = 100
REST_MAX_PAGES = 0             # 0 = sınırsız
CRAWL_ENABLED = True           # sitemap/REST boş dönerse HTML taramasına düş

# --------------------------------------------------------------------------- #
# Medya çözme (video kaynağı bulma)
# --------------------------------------------------------------------------- #
# Film sayfasındaki gömme (embed) adresi kalıbı.
EMBED_PATTERNS = ["/pornolar/{id}.html", "/embed/{id}", "/player/{id}.html"]

# Video id'sini bulmak için denenen kalıplar (sırayla).
VIDEO_ID_REGEXES = [
    r"/pornolar/(\d+)\.html",
    r"/(?:embed|player|video|film|izle)/(\d{3,8})",
    r"[?&](?:id|vid|media[_-]?id)=(\d{3,8})",
    r"/(\d{4,8})\.mp4",
    r"""data-(?:video-)?id=["'](\d{3,8})["']""",
]

# Bilinen/öğrenilen CDN alan adları.  Yenileri tarama sırasında öğrenilir ve
# media_cache.json içine yazılır; buradakiler sadece ilk tohumdur.
CDN_HOSTS = ["cdn.evolliecdnsx.com"]

# {host} ve {id} yer tutucuları kullanılır.
CDN_VIDEO_TEMPLATES = [
    "https://{host}/{id}.mp4",
    "https://{host}/videos/{id}.mp4",
    "https://{host}/mp4/{id}.mp4",
    "https://{host}/{id}/index.m3u8",
]
CDN_POSTER_TEMPLATES = [
    "https://{host}/img/{id}.jpg",
    "https://{host}/thumbs/{id}.jpg",
    "https://{host}/{id}.jpg",
]

VERIFY_STREAMS = True          # tarama sırasında kaynakları doğrula
VERIFY_SAMPLE = 60             # her taramada doğrulanacak film sayısı (0 = hepsi)
PROBE_TIMEOUT = (6, 25)        # (bağlanma, okuma)
OPEN_TIMEOUT = (5, 30)         # video akışını açarken (bağlanma, okuma)

# Erişilemeyen alan adları için devre kesici: aynı sunucuya defalarca
# zaman aşımı beklemek yerine kısa süreliğine atla (oynatma gecikmesin).
HOST_FAIL_THRESHOLD = 2        # bu kadar bağlantı hatasından sonra alan adını beklet
HOST_COOLDOWN = 90             # saniye
PROBE_BYTES = 2048
CACHE_TTL_OK = 12 * 3600       # başarılı çözümün geçerlilik süresi
CACHE_TTL_FAIL = 600           # başarısız çözüm tekrar deneme süresi

# --------------------------------------------------------------------------- #
# Zaman aşımı / tekrar
# --------------------------------------------------------------------------- #
TIMEOUT = (10, 30)
HTTP_RETRIES = 2               # sayfa indirmelerinde tekrar deneme
RESOLVER_RETRIES = 0           # kaynak denemelerinde tekrar yok: hızlı düş, sıradaki adaya geç
BACKOFF_FACTOR = 0.6

# --------------------------------------------------------------------------- #
# Arayüz
# --------------------------------------------------------------------------- #
VIDEOS_PER_PAGE_HTML = 100

# --------------------------------------------------------------------------- #
# Dosyalar
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILMS_JSON = os.path.join(BASE_DIR, "films.json")
PLAYLIST_M3U = os.path.join(BASE_DIR, "playlist.m3u")
LOCAL_PLAYLIST_M3U = os.path.join(BASE_DIR, "playlist_local.m3u")
MEDIA_CACHE = os.path.join(BASE_DIR, "media_cache.json")

# Tarama sırasında filmlerin kaydedilme sıklığı
SAVE_EVERY = 25

# films.json biçimlenmiş (okunaklı) mı, kompakt mı yazılsın?
# Büyük kataloglarda kompakt biçim ~%40 daha küçük ve daha hızlı yüklenir.
PRETTY_JSON = False


def _apply_env():
    """EVOLI_<ADI> ortam değişkenleriyle config'i geçersiz kıl."""
    g = globals()
    for key, val in list(g.items()):
        if key.startswith("_") or not key.isupper():
            continue
        env = os.environ.get("EVOLI_" + key)
        if env is None:
            continue
        if isinstance(val, bool):
            g[key] = env.strip().lower() in ("1", "true", "yes", "evet", "on")
        elif isinstance(val, int) and not isinstance(val, bool):
            try:
                g[key] = int(env)
            except ValueError:
                pass
        elif isinstance(val, float):
            try:
                g[key] = float(env)
            except ValueError:
                pass
        elif isinstance(val, tuple):
            parts = [p for p in env.split(",") if p]
            if len(parts) == len(val):
                casted = []
                for p, v in zip(parts, val):
                    casted.append(int(p) if isinstance(v, int) else p)
                g[key] = tuple(casted)
        elif isinstance(val, list):
            g[key] = [p.strip() for p in env.split("|") if p.strip()]
        else:
            g[key] = env


_apply_env()
