# -*- coding: utf-8 -*-
"""Tam site tarayıcı: tüm kategoriler + tüm sayfalar"""
import re, json, time, ssl, os, sys
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

EXCLUDE = ['/page/', '/author/', '/kanal/', '/arsiv/',
           'iletisim', 'hakkimizda', 'dmca', '#comment', 'wp-content']


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


def fetch_all_categories():
    """Ana sayfadan tüm kategori linklerini ve isimlerini çeker"""
    try:
        r = session.get(BASE_URL + "/", timeout=20, verify=False)
        s = BeautifulSoup(r.text, 'html.parser')
        cats = {}
        for a in s.find_all('a', href=True):
            h = a['href']
            if h.endswith('-o/') or h.endswith('-i/'):
                c = a.get_text(strip=True)
                if c and len(c) > 1 and c not in cats:
                    if not h.startswith('http'):
                        h = BASE_URL + h
                    cats[c] = h
        return cats
    except Exception as e:
        print(f"  [!] Kategori tarama hatası: {e}")
        return {}


def get_total_pages(html):
    """Sayfadaki 'Sayfa X / Y' bilgisinden toplam sayfa sayısını bul"""
    # Örnek: "Sayfa 1 / 625" veya "Page 1 of 625"
    m = re.search(r'(?:Sayfa|Page)\s*\d+\s*(?:/|of)\s*(\d+)', html, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Yoksa pagination'daki son sayfa linkine bak
    m = re.search(r'/page/(\d+)/?"', html)
    if m:
        return int(m.group(1))
    return 1


def extract_video_links(html):
    """Sayfa HTML'inden video linklerini çıkarır"""
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        h = a['href']
        if not h.startswith('http'):
            h = BASE_URL + h
        if any(k in h.lower() for k in EXCLUDE):
            continue
        if h.endswith('-o/') or h.endswith('-i/'):
            continue
        if ('evooli.com' in h or BASE_URL in h) and len(h) > 40:
            links.append(h)
    return list(dict.fromkeys(links))


def fetch_film(link):
    """Tek bir video linkinden film bilgisi çıkarır"""
    try:
        r = session.get(link, timeout=20, verify=False)
        if r.status_code != 200:
            return None
        s = BeautifulSoup(r.text, 'html.parser')
        h1 = s.find('h1')
        title = h1.get_text(strip=True) if h1 else link.rstrip('/').split('/')[-1].replace('-', ' ').title()
        cats = []
        for a in s.find_all('a', href=True):
            h = a['href']
            if h.endswith('-o/') or h.endswith('-i/'):
                c = a.get_text(strip=True)
                if c and c not in cats and len(c) > 1:
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
        return None


def scan_page(url, existing, stats):
    """Tek bir sayfayı tarar, yeni filmleri existing dict'ine ekler"""
    try:
        r = session.get(url, timeout=20, verify=False)
        if r.status_code != 200:
            return
        links = extract_video_links(r.text)
        stats["pages"] += 1
        stats["found"] += len(links)
        for lk in links:
            if stats["scanned"] % 10 == 0:
                print(f"    ~ Taranan: {stats['scanned']} | Yeni: {stats['new']}")
            stats["scanned"] += 1
            # Hız için ID kontrol et (link'ten ID tahmin et)
            m = re.search(r'/(\d+)/?$', lk)
            potential_id = m.group(1) if m else None
            if potential_id and potential_id in existing:
                continue
            f = fetch_film(lk)
            if f and f["id"] not in existing:
                existing[f["id"]] = f
                stats["new"] += 1
            time.sleep(REQUEST_DELAY)
    except Exception as e:
        print(f"    [!] Sayfa hatası: {e}")


def scan(max_pages=PAGES_PER_SCAN, scan_categories=SCAN_ALL_CATEGORIES):
    """Ana tarama fonksiyonu"""
    db = load_db()
    existing = {f["id"]: f for f in db["films"]}
    stats = {"pages": 0, "scanned": 0, "new": 0, "found": 0}
    start_time = time.time()

    # 1. Kategorileri tarayarak kaydet
    print("\n[1/3] Kategoriler keşfediliyor...")
    all_cats = fetch_all_categories()
    db["categories"] = all_cats
    print(f"  [✓] {len(all_cats)} kategori bulundu:")
    for c in list(all_cats.keys())[:10]:
        print(f"      - {c}")
    if len(all_cats) > 10:
        print(f"      ... ve {len(all_cats) - 10} kategori daha")

    # 2. Ana sayfa + sayfalama (yeni eklenenler burada)
    print(f"\n[2/3] Ana sayfa taranıyor (ilk {max_pages} sayfa)...")
    for page in range(1, max_pages + 1):
        url = BASE_URL + "/" if page == 1 else f"{BASE_URL}/page/{page}/"
        print(f"  [~] Sayfa {page}: {url}")
        scan_page(url, existing, stats)
        save_db({**db, "films": list(existing.values())})  # Her sayfada kaydet (güvenlik)

    # 3. Kategorileri tara
    if scan_categories and all_cats:
        print(f"\n[3/3] Kategoriler taranıyor...")
        cat_list = list(all_cats.items())
        for idx, (cat_name, cat_url) in enumerate(cat_list, 1):
            print(f"\n  [{idx}/{len(cat_list)}] {cat_name}")
            print(f"      URL: {cat_url}")
            try:
                r = session.get(cat_url, timeout=20, verify=False)
                total_pages = get_total_pages(r.text)
                pages_to_scan = total_pages
                if MAX_PAGES_PER_CATEGORY > 0:
                    pages_to_scan = min(total_pages, MAX_PAGES_PER_CATEGORY)
                print(f"      Toplam {total_pages} sayfa, {pages_to_scan} taranacak")
                
                for page in range(1, pages_to_scan + 1):
                    page_url = cat_url if page == 1 else f"{cat_url.rstrip('/')}page/{page}/"
                    print(f"      Sayfa {page}/{pages_to_scan}")
                    scan_page(page_url, existing, stats)
                    
                    # Her 5 kategoride bir kaydet
                    if idx % 5 == 0:
                        save_db({**db, "films": list(existing.values())})
            except Exception as e:
                print(f"      [!] Kategori hatası: {e}")

    # Son kaydet
    db["films"] = sorted(existing.values(), key=lambda x: x.get("added", ""), reverse=True)
    save_db(db)
    write_m3u(db)

    elapsed = time.time() - start_time
    print(f"\n{'='*55}")
    print(f"[✓] TARAMA TAMAMLANDI!")
    print(f"    Süre: {elapsed/60:.1f} dakika")
    print(f"    Taranan sayfa: {stats['pages']}")
    print(f"    Taranan link: {stats['scanned']}")
    print(f"    Bulunan link: {stats['found']}")
    print(f"    Yeni film: {stats['new']}")
    print(f"    Toplam film: {len(db['films'])}")
    print(f"{'='*55}\n")
    return stats["new"]


if __name__ == "__main__":
    print("Tam tarama başlıyor... (Ctrl+C ile durdurabilirsin)")
    print("Durdurduğunda kaydedilmiş veriler kaybolmaz.\n")
    try:
        scan()
    except KeyboardInterrupt:
        print("\n\n[!] Durduruldu. Veriler kaydedildi.")
