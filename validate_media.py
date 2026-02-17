#!/usr/bin/env python3
import json
import sys
import shutil
import logging
from pathlib import Path
from datetime import datetime
from multiprocessing import Process, Queue
from typing import Optional

# Konfiguration
MEDIA_CHECK_TIMEOUT = 15.0   # Sekunden Timeout pro Datei
LOG_ENABLED = False         # wird in main() durch -l gesetzt


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------

def setup_logging(log_file: Path) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Wichtig: mode='a' statt 'w' -> anhängen statt überschreiben[web:123]
    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Optional: Lauf-Trenner ins Log schreiben
    logger.info("==================================================")
    logger.info("Neuer Lauf gestartet")

def log_print(msg: str) -> None:
    if LOG_ENABLED:
        logging.info(msg)
    else:
        print(msg)


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------

def check_dependencies() -> bool:
    """
    Prüft, ob Pillow und OpenCV installiert sind.
    """
    missing = []

    try:
        import PIL  # noqa
        log_print("✓ Pillow ist installiert")
    except ImportError:
        missing.append("pillow")
        log_print("✗ Pillow fehlt")

    try:
        import cv2  # noqa
        log_print("✓ OpenCV ist installiert")
    except ImportError:
        missing.append("opencv-python")
        log_print("✗ OpenCV fehlt")

    if missing:
        log_print("\nFehlende Pakete installieren, z.B.:")
        log_print(f"  pip install {' '.join(missing)}")
        return False

    log_print("✓ Alle erforderlichen Pakete sind installiert")
    return True

def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def next_free_name(path: Path) -> Path:
    """
    Wenn path existiert, anhängen von _1, _2, ... vor der Extension.
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ----------------------------------------------------------------------
# Medienprüfung (Worker + Wrapper mit Timeout)
# ----------------------------------------------------------------------

def _check_media_worker(path: Path, q: Queue) -> None:
    """
    Läuft im separaten Prozess.
    Schreibt True (gültig) oder False (ungültig) in die Queue.
    """
    suffix = path.suffix.lower()
    try:
        # Bildformate
        if suffix in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"]:
            from PIL import Image
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                img.load()
            q.put(True)
            return

        # Videoformate
        if suffix in [".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".m4v"]:
            import cv2
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                q.put(False)
                return
            ret, frame = cap.read()
            cap.release()
            q.put(bool(ret and frame is not None))
            return

        # unbekanntes Format -> ungültig
        q.put(False)
    except Exception:
        q.put(False)

def is_valid_media(path: Path, timeout: float = MEDIA_CHECK_TIMEOUT) -> Optional[bool]:
    """
    Prüft mit Timeout, ob Datei ein gültiges Bild/Video ist.

    Rückgabewerte:
      True  = gültig
      False = ungültig
      None  = Prüfung abgebrochen (Timeout oder interner Fehler)
    """
    q: Queue = Queue(maxsize=1)
    p = Process(target=_check_media_worker, args=(path, q))
    p.start()
    p.join(timeout)

    if p.is_alive():
        # Timeout: Prozess abbrechen
        p.terminate()
        p.join()
        log_print(f"  Prüfung abgebrochen (Timeout nach {timeout:.1f}s)")
        return None

    try:
        result = q.get_nowait()
    except Exception:
        log_print("  Prüfung abgebrochen (kein Ergebnis aus Worker)")
        return None

    return bool(result)


# ----------------------------------------------------------------------
# Hauptlogik: JSON-basierte Verarbeitung
# ----------------------------------------------------------------------

def rename_media_files(json_data, base_dir: Path, move_mode: bool = False) -> None:
    """
    Verarbeitet Mediendateien aus der ProjectVic-JSON.

    move_mode=False:
        - gültige Dateien werden im Medienordner umbenannt
        - ungültige Dateien werden gelöscht

    move_mode=True:
        - gültige Dateien werden nach base_dir/valid/ verschoben (mit neuem Namen)
        - ungültige Dateien werden nach base_dir/invalid/ verschoben
    """
    if move_mode:
        valid_dir = base_dir / "valid"
        invalid_dir = base_dir / "invalid"
        valid_dir.mkdir(exist_ok=True)
        invalid_dir.mkdir(exist_ok=True)
        log_print("Move-Modus aktiv")
        log_print(f"  Valid:   {valid_dir}")
        log_print(f"  Invalid: {invalid_dir}")

    valid_count = 0
    invalid_count = 0
    skipped_timeout = 0

    for case in json_data.get("value", []):
        for media in case.get("Media", []):
            rel_path = media.get("RelativeFilePath")
            media_files = media.get("MediaFiles") or []
            if not rel_path or not media_files:
                continue

            file_name = media_files[0].get("FileName")
            if not file_name:
                continue

            old_path = base_dir / Path(rel_path.replace("\\", "/"))
            if not old_path.exists():
                log_print(f"Warnung: Datei nicht gefunden: {old_path}")
                continue

            log_print(f"\nPrüfe: {old_path}")

            check_result = is_valid_media(old_path)

            # None = Timeout / Fehler -> Datei unangetastet lassen
            if check_result is None:
                skipped_timeout += 1
                log_print("  -> Prüfung ohne Ergebnis (Timeout/Fehler), Datei bleibt unverändert")
                continue

            # Ungültig (False)
            if not check_result:
                invalid_count += 1
                if move_mode:
                    dest = next_free_name((base_dir / "invalid") / old_path.name)
                    log_print(f"  -> Verschiebe nach invalid/: {dest.name}")
                    try:
                        shutil.move(str(old_path), str(dest))
                        log_print("  -> Erfolgreich verschoben")
                    except Exception as e:
                        log_print(f"  -> Fehler beim Verschieben: {e}")
                else:
                    log_print(f"  -> Lösche Datei: {old_path}")
                    try:
                        old_path.unlink()
                        log_print("  -> Erfolgreich gelöscht")
                    except Exception as e:
                        log_print(f"  -> Fehler beim Löschen: {e}")
                continue

            # Gültig (True)
            valid_count += 1
            if move_mode:
                desired_dest = (base_dir / "valid") / file_name
                dest = next_free_name(desired_dest)
                log_print(f"  -> Verschiebe nach valid/: {dest.name}")
                try:
                    shutil.move(str(old_path), str(dest))
                    log_print("  -> Erfolgreich verschoben")
                except Exception as e:
                    log_print(f"  -> Fehler beim Verschieben: {e}")
            else:
                desired_new = old_path.with_name(file_name)
                new_path = next_free_name(desired_new)
                log_print(f"  -> Benenne um: {new_path.name}")
                old_path.rename(new_path)

    log_print("\n=== Statistik ===")
    log_print(f"Gültige Dateien:          {valid_count}")
    log_print(f"Ungültige Dateien:        {invalid_count}")
    log_print(f"Mit Timeout übersprungen: {skipped_timeout}")
    log_print(f"Gesamt (bewertet):        {valid_count + invalid_count}")


# ----------------------------------------------------------------------
# Cleanup-Modus (rekursiv, unabhängig von JSON)
# ----------------------------------------------------------------------

def cleanup_directory(directory: Path) -> None:
    """
    Durchsucht ein Verzeichnis rekursiv und löscht alle ungültigen Mediendateien.
    """
    log_print(f"\nBereinige Verzeichnis: {directory}")
    deleted_count = 0

    for file_path in directory.rglob("*"):
        if not file_path.is_file():
            continue

        log_print(f"\nPrüfe: {file_path}")
        check_result = is_valid_media(file_path)

        if check_result is None:
            log_print("  -> Prüfung ohne Ergebnis (Timeout/Fehler), Datei bleibt unverändert")
            continue

        if not check_result:
            log_print(f"  -> Lösche ungültige Datei: {file_path}")
            try:
                file_path.unlink()
                deleted_count += 1
                log_print("  -> Erfolgreich gelöscht")
            except Exception as e:
                log_print(f"  -> Fehler beim Löschen: {e}")

    log_print(f"\n{deleted_count} ungültige Datei(en) gelöscht.")


# ----------------------------------------------------------------------
# CLI / main
# ----------------------------------------------------------------------

def print_help(prog: str) -> None:
    print(f"""Verwendung:
  {prog} <case.json>
      Standard: Gültige Dateien umbenennen, ungültige löschen.

  {prog} -m <case.json>
      Move-Modus: Gültige nach ./valid/, ungültige nach ./invalid/ verschieben.

  {prog} -c <verzeichnis>
      Cleanup-Modus: Verzeichnis rekursiv prüfen, ungültige Dateien löschen.

  {prog} -p
      Paket-Abhängigkeiten (Pillow, OpenCV) prüfen.

Optionen:
  -l
      Optional: Logging in Logdatei aktivieren.
      Bei JSON: <case>.log
      Bei -c:   <verzeichnis>_cleanup.log
      Bei -p:   dependency_check.log

Funktion:
  Liest eine ProjectVic-JSON-Datei ein, sucht zugehörige Mediendateien
  relativ zum Speicherort der JSON, prüft sie und benennt sie um.
  Ungültige Dateien werden gelöscht, bei Nutzung von -m verschoben.

Details:
  - Für jeden Eintrag unter "Media" wird die Datei aus "RelativeFilePath"
    gesucht.
  - Ist die Datei kein gültiges Bild/Video oder hat ein unbekanntes Format,
    wird sie gelöscht (Standard-Modus) oder nach invalid/ verschoben (-m).
  - Ist die Datei gültig, wird sie in den "FileName" aus "MediaFiles[0]"
    umbenannt (Standard) oder nach valid/ verschoben (-m).
  - Falls der gewünschte Zielname bereits existiert, wird "_<Nummer>"
    vor der Dateiendung angehängt (z.B. foo_1.mp4, foo_2.mp4, ...).

Optionen:
  -h, --help        Diese Hilfe anzeigen
  -p                Paket-Abhängigkeiten prüfen
  -c <verzeichnis>  Alle ungültigen Dateien in einem Verzeichnis löschen
                    (Rekursiv! Keine Warnung! Verzeichnis prüfen!)
  -m <case.json>    Move-Modus: Verschiebt Dateien nach valid/ oder invalid/
                    statt zu löschen/umzubenennen
  -l (optional)     Logging nach <case>.log (andernfalls nur Ausgabe)
  <case.json>       Alle Dateien umbenennen, ungültige löschen

Hinweise:
  - Die Medienprüfung hat einen Timeout von {MEDIA_CHECK_TIMEOUT:.1f}s pro Datei.
  - Bei Timeout/Fehler wird die Datei nicht verändert (nur protokolliert).
""")


def main() -> None:
    global LOG_ENABLED

    prog = Path(sys.argv[0]).name
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print_help(prog)
        sys.exit(0)

    # Logging-Flag auswerten
    LOG_ENABLED = "-l" in args
    args = [a for a in args if a != "-l"]

    if not args:
        print_help(prog)
        sys.exit(1)

    # Modi erkennen
    dep_check = False
    cleanup_dir: Optional[Path] = None
    json_path: Optional[Path] = None
    move_mode = False

    if args[0] == "-p":
        dep_check = True
    elif args[0] == "-c" and len(args) == 2:
        cleanup_dir = Path(args[1])
    elif args[0] == "-m" and len(args) == 2:
        move_mode = True
        json_path = Path(args[1])
    elif len(args) == 1:
        json_path = Path(args[0])
    else:
        print_help(prog)
        sys.exit(1)

    # Paketprüfung (-p)
    if dep_check:
        if LOG_ENABLED:
            setup_logging(Path("dependency_check.log"))
            log_print("Starte Paketprüfung (mit Logdatei)")
        check_dependencies()
        sys.exit(0)

    # Cleanup-Modus (-c)
    if cleanup_dir is not None:
        if not cleanup_dir.exists() or not cleanup_dir.is_dir():
            print(f"Fehler: Verzeichnis nicht gefunden: {cleanup_dir}")
            sys.exit(1)
        if LOG_ENABLED:
            log_file = cleanup_dir.parent / f"{cleanup_dir.name}_cleanup.log"
            setup_logging(log_file)
            log_print(f"Log-Datei: {log_file}")
        cleanup_directory(cleanup_dir)
        log_print("Fertig!")
        sys.exit(0)

    # JSON-Modi (Standard oder -m)
    if json_path is None or not json_path.exists():
        print(f"Fehler: JSON-Datei nicht gefunden: {json_path}")
        sys.exit(1)

    if LOG_ENABLED:
        log_file = json_path.with_suffix(".log")
        setup_logging(log_file)
        log_print(f"Log-Datei: {log_file}")

    log_print(f"JSON-Datei: {json_path}")
    log_print(f"Modus: {'Move' if move_mode else 'Rename/Delete'}")
    log_print(f"Timeout pro Datei: {MEDIA_CHECK_TIMEOUT:.1f}s")

    base_dir = json_path.parent.resolve()
    data = load_json(json_path)
    rename_media_files(data, base_dir, move_mode=move_mode)
    log_print("Fertig!")


if __name__ == "__main__":
    main()
