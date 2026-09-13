# -*- coding: utf-8 -*-
"""Evoli Film Bot — giriş noktası.

İnteraktif menü ya da komut satırı:

    python bot.py              # menü
    python bot.py scan         # hızlı güncelleme (yeni filmler)
    python bot.py full         # tam arşiv taraması (sitemap + REST)
    python bot.py server       # sadece sunucu
    python bot.py repair       # ölü/çalışmayan video kaynaklarını onar
    python bot.py migrate      # eski films.json'u yeni şemaya taşı (internetsiz)
    python bot.py status       # katalog özeti
    python bot.py auto         # tara + sunucu + zamanlayıcı
"""
from __future__ import annotations

import json
import sys
import threading
import time

import config
import scraper
import server


def scheduler_loop():
    print(f"[i] Zamanlayıcı aktif — her {config.SCAN_INTERVAL_HOURS} saatte bir tarama", flush=True)
    while True:
        time.sleep(config.SCAN_INTERVAL_HOURS * 3600)
        try:
            print(f"\n[~] Otomatik tarama — {time.strftime('%H:%M')}", flush=True)
            scraper.scan()
            # Yeni taramadan sonra bozuk görünen kaynakları arka planda onar.
            scraper.repair(limit=config.VERIFY_SAMPLE or 50, only_broken=True, verbose=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Hata: {exc}", flush=True)


def print_status():
    info = scraper.status()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    total = info.get("total") or 0
    ok = info.get("ok") or 0
    unknown = info.get("unknown") or 0
    broken = info.get("broken") or 0
    print(f"\n[✓] {total} film • doğrulanmış {ok} • doğrulanmamış {unknown} • sorunlu {broken}")
    if broken or unknown:
        print("[i] Sorunlu kayıtlar için: python bot.py repair")


def ask(prompt: str, default: str = "") -> str:
    try:
        value = input(prompt).strip()
    except EOFError:
        return default
    return value or default


def interactive():
    print("=" * 58)
    print("   EVOLI FILM BOT • v6")
    print("=" * 58)
    print(" 1) Hızlı Güncelleme      — yeni filmleri ekle (sitemap/REST)")
    print(" 2) Tam Arşiv Taraması    — sitedeki tüm filmleri indir (uzun sürer)")
    print(" 3) Sunucuyu Başlat       — premium arayüz + video proxy")
    print(" 4) Full Otomasyon        — tara + sunucu + zamanlayıcı")
    print(" 5) Kaynakları Onar       — oynamayan videoları yeniden çöz")
    print(" 6) Katalog Durumu        — özet + CDN/site bilgisi")
    print(" 7) Kataloğu Taşı (migrate) — eski films.json'u yeni şemaya çevir")
    choice = ask("Seçimin [1-7]: ")

    if choice == "1":
        limit = ask(f"Kaç yeni film? [boş={config.MAX_FILMS_PER_SCAN}]: ")
        scraper.scan(limit=int(limit) if limit.isdigit() else None)
    elif choice == "2":
        print("[!] Tam arşiv taraması uzun sürebilir. Ctrl+C ile durdurursan bulunanlar kaydedilir.")
        confirm = ask("Devam edilsin mi? [e/h]: ", "e").lower()
        if confirm.startswith(("e", "y")):
            scraper.scan(full=True, limit=0)
        else:
            print("Vazgeçildi.")
    elif choice == "3":
        server.run()
    elif choice == "4":
        scraper.scan()
        threading.Thread(target=scheduler_loop, daemon=True).start()
        server.run()
    elif choice == "5":
        limit = ask("Kaç kayıt onarılsın? [boş=hepsi]: ")
        only = ask("Sadece sorunlu kayıtlar mı? [e/h]: ", "e").lower().startswith(("e", "y"))
        scraper.repair(limit=int(limit) if limit.isdigit() else None, only_broken=only)
    elif choice == "6":
        print_status()
    elif choice == "7":
        scraper.migrate_db()
    else:
        print("Geçersiz seçim.")


def main(argv):
    command = (argv[0].lower() if argv else "").strip("-/")
    aliases = {
        "1": "scan", "tara": "scan", "scan": "scan", "update": "scan",
        "2": "full", "full": "full", "tam": "full", "arsiv": "full",
        "3": "server", "server": "server", "sunucu": "server", "serve": "server",
        "4": "auto", "auto": "auto", "otomasyon": "auto",
        "5": "repair", "repair": "repair", "onar": "repair", "fix": "repair",
        "6": "status", "status": "status", "durum": "status",
        "7": "migrate", "migrate": "migrate", "tasir": "migrate", "taşı": "migrate",
    }
    action = aliases.get(command, "")
    if not command:
        interactive()
        return
    if not action:
        print(f"Bilinmeyen komut: {command}")
        print("Kullanım: python bot.py [scan|full|server|auto|repair|migrate|status]")
        return
    if action == "scan":
        scraper.scan()
    elif action == "full":
        scraper.scan(full=True, limit=0)
    elif action == "server":
        server.run()
    elif action == "auto":
        scraper.scan()
        threading.Thread(target=scheduler_loop, daemon=True).start()
        server.run()
    elif action == "repair":
        scraper.repair()
    elif action == "migrate":
        scraper.migrate_db()
    elif action == "status":
        print_status()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("\n[*] Durduruldu — kaydedilen veriler güvende.")
