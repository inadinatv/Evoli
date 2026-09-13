# -*- coding: utf-8 -*-
import threading, time, sys
from scraper import scan
import server
from config import SCAN_INTERVAL_HOURS


def scheduler_loop():
    print(f"[i] Zamanlayıcı aktif — her {SCAN_INTERVAL_HOURS} saatte bir tarama")
    while True:
        time.sleep(SCAN_INTERVAL_HOURS * 3600)
        try:
            print(f"\n[~] Otomatik tarama — {time.strftime('%H:%M')}")
            scan()
        except Exception as e:
            print(f"[!] Hata: {e}")


def main():
    print("=" * 50)
    print("   FILMBOT v4.0")
    print("=" * 50)
    print(" 1) Siteyi Tara (films.json + playlist.m3u üretir)")
    print(" 2) Yerel Sunucuyu Başlat")
    print(" 3) Full Otomasyon (tara + sunucu + zamanlayıcı)")
    sec = input("Seçimin [1/2/3]: ").strip()
    if sec == "1":
        n = input("Kaç sayfa? [boş=5]: ").strip()
        scan(int(n) if n.isdigit() else 5)
    elif sec == "2":
        server.run()
    elif sec == "3":
        scan()
        threading.Thread(target=scheduler_loop, daemon=True).start()
        server.run()
    else:
        print("Geçersiz.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Durduruldu.")
