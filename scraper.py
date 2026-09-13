# -*- coding: utf-8 -*-
import re, json, time, ssl, os
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
import urllib3
import urllib.parse
from config import *

urllib3.disable_warnings()

class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

session = requests.Session()
session.mount('https://', SSLAdapter())
session.mount('http://', SSLAdapter())
session.headers.update({"User-Agent": USER_AGENT})

EXCLUDE = ['/page/', '/author/', '/kanal/',
           'iletisim', 'hakkimizda', 'dmca', '#comment']

# Tüm kategorileri tara
def fetch_all_categories():
    """Ana sayfadan tüm kategori linklerini ve isimlerini çek"""
    try:
        r = session.get(BASE_URL + "/", timeout=20, verify=False)
        s = BeautifulSoup(r.text, 'html.parser')
        cats = {}
        for a in s.find_all('a', href=True):
            h = a['href']
            if h.endswith('-o/') or h.endswith('-i/'):
                c = a.get_text(strip=True)
                if c and len(c) > 2:
                    if not h.startswith('http'):
                        h = BASE_URL + h
                    cats[c] = h
        return cats
    except Exception as e:
        print(f"  [!] Kategori tarama hatası: {e}")
        return {}

def load_db():
    if os.path.exists(FILMS_JSON):
        try:
            with open(FILMS_JSON, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"updated": "", "films": [], "categories": {}}


def save_db(db):
    db["updated"] = time.strftime("%Y-%m-%d %H:%M")
    db["total"] = len(db["films"])
    with open(FILMS_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)


def write_m3u(db):
    ua_enc = urllib.parse.quote(USER_AGENT)
    ref_enc = urllib.parse.quote(REFERER)
    with open(PLAYLIST_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for x in db["films"]:
            cats = x.get('categories', [])
            group = ','.join(cats[:3]) if cats else "Genel"
            f.write(f"#EXTINF:-1 group-title=\"{group}\",{x['title']}\n")
            f.write(f"{x['stream']}|User-Agent={ua_enc}|Referer={ref_enc}\n")


def fetch_film(link):
    try:
        r = session.get(link, timeout=20, verify=False)
        s = BeautifulSoup(r.text, 'html.parser')
        h1 = s.find('h1')
        title = h1.get_text(strip=True) if h1 else link.rstrip('/').split('/')[-1].replace('-', ' ').title()
        cats = []
        for a in s.find_all('a', href=True):
            h = a['href']
            if h.endswith('-o/') or h.endswith('-i/'):
                c = a.get_text(strip=True)
                if c and c not in cats and len(c) > 2:
                    cats.append(c)
        ifr = s.find('iframe')
        if not ifr:
            return None
        src = ifr.get('src', '')
        m = re.search(r'/pornolar/(\d+)\.html', src)
        fid = m.group(1) if m else str(abs(hash(src)) % 1000000)
        if src.startswith('/'):
            src = BASE_URL + src
        r2 = session.get(src, timeout=20, verify=False)
        mm = re.search(r'(https?://[^"\'\s<>]+\.mp4[^"\'\s<>]*)', r2.text)
        if not mm:
            return None
        im = re.search(r'image:\s*["\']([^"\']+)["\']', r2.text)
        return {
            "id": fid,
            "title": title,
            "stream": mm.group(1),
            "poster": im.group(1) if im else "",
            "categories": cats,
            "url": link,
            "added": time.strftime("%Y-%m-%d %H:%M")
        }
    except Exception as e:
        print(f"  [!] {link} hatası: {e}")
        return None


def scan(max_pages=PAGES_PER_SCAN, scan_categories=True):
    db = load_db()
    existing = {f["id"]: f for f in db["films"]}
    new_count = 0
    
    # Kategorileri tara ve kaydet
    if scan_categories:
        print("[*] Kategoriler taranıyor...")
        all_cats = fetch_all_categories()
        db["categories"] = all_cats
        print(f"  [✓] {len(all_cats)} kategori bulundu")
    
    # Ana sayfa ve sayfalama
    for page in range(1, max_pages + 1):
        url = BASE_URL + "/" if page == 1 else f"{BASE_URL}/page/{page}/"
        print(f"  [~] Sayfa {page}: {url}")
        try:
            r = session.get(url, timeout=20, verify=False)
            soup = BeautifulSoup(r.text, 'html.parser')
            links = []
            for a in soup.find_all('a', href=True):
                h = a['href']
                if not h.startswith('http'):
                    h = BASE_URL + h
                if any(k in h.lower() for k in EXCLUDE):
                    continue
                if h.endswith('-o/') or h.endswith('-i/'):
                    continue
                if 'evooli.com' in h and len(h) > 40:
                    links.append(h)
            links = list(dict.fromkeys(links))
            for lk in links:
                f = fetch_film(lk)
                if f and f["id"] not in existing:
                    existing[f["id"]] = f
                    new_count += 1
                    print(f"  [+] YENİ: {f['title'][:60]}")
                time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"  [!] Sayfa hatası: {e}")
    
    # Kategori sayfalarını da tara (opsiyonel, çok uzun sürer)
    if scan_categories and db.get("categories"):
        print("[*] Kategori sayfaları taranıyor (ilk 5 kategori)...")
        cat_list = list(db["categories"].items())[:5]
        for cat_name, cat_url in cat_list:
            print(f"  [~] Kategori: {cat_name}")
            try:
                r = session.get(cat_url, timeout=20, verify=False)
                soup = BeautifulSoup(r.text, 'html.parser')
                links = []
                for a in soup.find_all('a', href=True):
                    h = a['href']
                    if not h.startswith('http'):
                        h = BASE_URL + h
                    if any(k in h.lower() for k in EXCLUDE):
                        continue
                    if h.endswith('-o/') or h.endswith('-i/'):
                        continue
                    if 'evooli.com' in h and len(h) > 40:
                        links.append(h)
                links = list(dict.fromkeys(links))[:20]  # Her kategoriden max 20
                for lk in links:
                    f = fetch_film(lk)
                    if f and f["id"] not in existing:
                        existing[f["id"]] = f
                        new_count += 1
                        print(f"    [+] {cat_name}: {f['title'][:50]}")
                    time.sleep(REQUEST_DELAY)
            except Exception as e:
                print(f"  [!] Kategori hatası: {e}")
    
    db["films"] = sorted(existing.values(), key=lambda x: x.get("added", ""), reverse=True)
    save_db(db)
    write_m3u(db)
    print(f"[✓] Tarama bitti. Yeni: {new_count} | Toplam: {len(db['films'])}")
    return new_count
