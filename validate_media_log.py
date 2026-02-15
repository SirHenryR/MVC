#!/usr/bin/env python3
import json
import sys
import shutil
import logging
from pathlib import Path
from datetime import datetime

def setup_logging(log_file):
    """
    Richtet Logging ein: Ausgabe auf Konsole und in Datei.
    """
    # Logger konfigurieren
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Format für Log-Einträge
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    
    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File Handler
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

def log_print(message):
    """Gibt Nachricht auf Konsole UND ins Log aus."""
    logging.info(message)

def check_dependencies():
    """
    Prüft, ob alle erforderlichen Pakete installiert sind.
    Gibt True zurück wenn alles OK, sonst False.
    """
    missing = []
    
    # Pillow prüfen
    try:
        import PIL
        log_print(f"✓ Pillow {PIL.__version__} ist installiert")
    except ImportError:
        missing.append("pillow")
        log_print("✗ Pillow fehlt")
    
    # OpenCV prüfen
    try:
        import cv2
        log_print(f"✓ OpenCV {cv2.__version__} ist installiert")
    except ImportError:
        missing.append("opencv-python")
        log_print("✗ OpenCV fehlt")
    
    if missing:
        log_print("\nFehlende Pakete installieren:")
        log_print("\nMit pip:")
        log_print(f"  pip install {' '.join(missing)}")
        log_print("\nMit MacPorts (Python 3.12 als Beispiel):")
        for pkg in missing:
            if pkg == "pillow":
                log_print("  sudo port install py312-pillow")
            elif pkg == "opencv-python":
                log_print("  sudo port install py312-opencv")
        return False
    
    log_print("\n✓ Alle erforderlichen Pakete sind installiert.")
    return True

def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def next_free_name(path):
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

def is_valid_media(path):
    """
    Prüft, ob die Datei ein gültiges Bild oder Video ist.
    Gibt True zurück wenn gültig, sonst False.
    """
    suffix = path.suffix.lower()
    
    # Bilder mit Pillow prüfen
    if suffix in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp']:
        try:
            from PIL import Image
            with Image.open(path) as img:
                img.verify()
            with Image.open(path) as img:
                img.load()
            return True
        except Exception as e:
            log_print(f"  Ungültiges Bild: {e}")
            return False
    
    # Videos mit OpenCV prüfen
    elif suffix in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v']:
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                log_print(f"  Video kann nicht geöffnet werden")
                return False
            
            ret, frame = cap.read()
            cap.release()
            
            if not ret or frame is None:
                log_print(f"  Kein gültiger Frame lesbar")
                return False
            
            return True
        except Exception as e:
            log_print(f"  Ungültiges Video: {e}")
            return False
    
    # Unbekanntes Format - LÖSCHEN
    else:
        log_print(f"  Unbekanntes Format {suffix}")
        return False

def rename_media_files(json_data, base_dir, move_mode=False):
    """
    Verarbeitet Mediendateien aus der JSON.
    
    move_mode=False: Löscht ungültige Dateien, benennt gültige um
    move_mode=True: Verschiebt gültige nach valid/, ungültige nach invalid/
    """
    if move_mode:
        valid_dir = base_dir / "valid"
        invalid_dir = base_dir / "invalid"
        valid_dir.mkdir(exist_ok=True)
        invalid_dir.mkdir(exist_ok=True)
        log_print(f"Move-Modus aktiv:")
        log_print(f"  Valid:   {valid_dir}")
        log_print(f"  Invalid: {invalid_dir}\n")
    
    valid_count = 0
    invalid_count = 0
    
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
            
            # Validierung: Ist die Datei ein gültiges Bild/Video?
            log_print(f"\nPrüfe: {old_path}")
            
            if not is_valid_media(old_path):
                invalid_count += 1
                if move_mode:
                    # In invalid/ verschieben, Original-Name behalten
                    dest = next_free_name(invalid_dir / old_path.name)
                    log_print(f"  -> Verschiebe nach invalid/: {dest.name}")
                    try:
                        shutil.move(str(old_path), str(dest))
                        log_print(f"  -> Erfolgreich verschoben")
                    except Exception as e:
                        log_print(f"  -> Fehler beim Verschieben: {e}")
                else:
                    # Löschen
                    log_print(f"  -> Lösche Datei: {old_path}")
                    try:
                        old_path.unlink()
                        log_print(f"  -> Erfolgreich gelöscht")
                    except Exception as e:
                        log_print(f"  -> Fehler beim Löschen: {e}")
                continue
            
            # Falls gültig
            valid_count += 1
            if move_mode:
                # Nach valid/ verschieben mit neuem Namen
                desired_dest = valid_dir / file_name
                dest = next_free_name(desired_dest)
                log_print(f"  -> Verschiebe nach valid/: {dest.name}")
                try:
                    shutil.move(str(old_path), str(dest))
                    log_print(f"  -> Erfolgreich verschoben")
                except Exception as e:
                    log_print(f"  -> Fehler beim Verschieben: {e}")
            else:
                # Umbenennen an Ort und Stelle
                desired_new = old_path.with_name(file_name)
                new_path = next_free_name(desired_new)
                log_print(f"  -> Benenne um: {new_path.name}")
                old_path.rename(new_path)
    
    log_print(f"\n=== Statistik ===")
    log_print(f"Gültige Dateien: {valid_count}")
    log_print(f"Ungültige Dateien: {invalid_count}")
    log_print(f"Gesamt: {valid_count + invalid_count}")

def cleanup_directory(directory):
    """
    Durchsucht ein Verzeichnis rekursiv und löscht alle ungültigen Mediendateien.
    """
    log_print(f"\nBereinige Verzeichnis: {directory}")
    deleted_count = 0
    
    for file_path in directory.rglob("*"):
        if not file_path.is_file():
            continue
        
        log_print(f"\nPrüfe: {file_path}")
        
        if not is_valid_media(file_path):
            log_print(f"  -> Lösche ungültige Datei: {file_path}")
            try:
                file_path.unlink()
                deleted_count += 1
                log_print(f"  -> Erfolgreich gelöscht")
            except Exception as e:
                log_print(f"  -> Fehler beim Löschen: {e}")
    
    log_print(f"\n{deleted_count} ungültige Datei(en) gelöscht.")

def print_help(prog):
    print(f"""Verwendung:
  {prog} <case.json>
  {prog} -m <case.json>
  {prog} -p
  {prog} -c <verzeichnis>

Funktion:
  Liest eine ProjectVic-JSON-Datei ein, sucht zugehörige Mediendateien
  relativ zum Speicherort der JSON, prüft sie und benennt sie um.

Details:
  - Für jeden Eintrag unter "Media" wird die Datei aus "RelativeFilePath"
    gesucht.
  - Ist die Datei kein gültiges Bild/Video oder hat ein unbekanntes Format,
    wird sie gelöscht (Standard-Modus) oder nach invalid/ verschoben (-m).
  - Ist die Datei gültig, wird sie in den "FileName" aus "MediaFiles[0]"
    umbenannt (Standard) oder nach valid/ verschoben (-m).
  - Falls der gewünschte Zielname bereits existiert, wird "_<Nummer>"
    vor der Dateiendung angehängt (z.B. foo_1.mp4, foo_2.mp4, ...).
  - Es wird automatisch eine Log-Datei <casefile>.log erstellt.

Optionen:
  -h, --help        Diese Hilfe anzeigen
  -p                Paket-Abhängigkeiten prüfen
  -c <verzeichnis>  Alle ungültigen Dateien in einem Verzeichnis löschen
  -m <case.json>    Move-Modus: Verschiebt Dateien nach valid/ oder invalid/
                    statt zu löschen/umbenennen
""")

def main():
    prog = Path(sys.argv[0]).name

    # Hilfe bei -h, --help
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print_help(prog)
        sys.exit(0)

    # Paket-Prüfung (ohne Logging)
    if len(sys.argv) == 2 and sys.argv[1] == "-p":
        # Temporäres Logging für -p
        setup_logging(Path("dependency_check.log"))
        check_dependencies()
        sys.exit(0)

    # Directory cleanup
    if len(sys.argv) == 3 and sys.argv[1] == "-c":
        cleanup_dir = Path(sys.argv[2])
        if not cleanup_dir.exists() or not cleanup_dir.is_dir():
            print(f"Fehler: Verzeichnis nicht gefunden: {cleanup_dir}")
            sys.exit(1)
        
        # Log-Datei für Cleanup
        log_file = cleanup_dir.parent / f"{cleanup_dir.name}_cleanup.log"
        setup_logging(log_file)
        log_print(f"Log-Datei: {log_file}")
        
        cleanup_directory(cleanup_dir)
        log_print("\nFertig!")
        sys.exit(0)

    # Move-Modus
    move_mode = False
    json_arg_index = 1
    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        move_mode = True
        json_arg_index = 2

    if len(sys.argv) < 2 or (move_mode and len(sys.argv) != 3) or (not move_mode and len(sys.argv) != 2):
        print_help(prog)
        sys.exit(1)
    
    json_path = Path(sys.argv[json_arg_index])
    if not json_path.exists():
        print(f"Fehler: JSON-Datei nicht gefunden: {json_path}")
        sys.exit(1)
    
    # Log-Datei basierend auf JSON-Dateinamen erstellen
    log_file = json_path.with_suffix('.log')
    setup_logging(log_file)
    
    log_print(f"=== Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    log_print(f"JSON-Datei: {json_path}")
    log_print(f"Log-Datei: {log_file}")
    log_print(f"Modus: {'Move' if move_mode else 'Rename/Delete'}\n")
    
    base_dir = json_path.parent.resolve()
    
    loaded_data = load_json(json_path)
    rename_media_files(loaded_data, base_dir, move_mode=move_mode)
    
    log_print(f"\n=== Ende: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    log_print("\nFertig!")

if __name__ == "__main__":
    main()
