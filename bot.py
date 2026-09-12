# -*- coding: utf-8 -*-
"""Ana program: scheduler + sunucu + CLI menü."""
import threading, time, sys
from scraper import scan, load_db
import server
from config import SCAN_INTERVAL_HOURS


def scheduler_loop():
    """Belirli aralıklarla siteyi tarar, JSON'a yeni filmleri ekler."""
    print(f"[i] Zamanlayıcı aktif — her {SCAN_INTERVAL_HOURS} saatte bir tarama")
    while True:
        time.sleep(SCAN_INTERVAL_HOURS * 3600)
        try:
            print(f"\n[~] Otomatik tarama başladı — {time.strftime('%H:%M')}")
            scan()
        except Exception as e:
            print(f"[!] Zamanlayıcı hatası: {e}")


def main():
    print("=" * 55)
    print("   🎬 FILMBOT v4.0 - OTOMATIK GÜNCEL KATALOG")
    print("=" * 55)
    print(" 1) Siteyi Tara (manuel)")
    print(" 2) Sunucuyu Başlat (sadece arayüz)")
    print(" 3) FULL OTOMASYON: Tarayıcı + Sunucu + Zamanlayıcı")
    sec = input("Seçimin [1/2/3]: ").strip()

    if sec == "1":
        n = input("Kaç sayfa? [boş=5]: ").strip()
        scan(int(n) if n.isdigit() else 5)

    elif sec == "2":
        server.run()

    elif sec == "3":
        # Önce ilk taramayı yap
        print("[*] İlk tarama başlıyor...")
        scan()
        # Zamanlayıcıyı arka planda başlat
        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()
        # Sunucuyu ana thread'de çalıştır (Ctrl+C ile durur)
        server.run()
    else:
        print("Geçersiz.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Bot durduruldu.")
