#!/opt/local/bin/python
import json
import sys
from pathlib import Path

def print_help(prog: str) -> None:
    print(f"""Verwendung:
  {prog} <case.json>

Funktion:
  Liest eine ProjectVic-JSON-Datei ein, sucht zugehörige Mediendateien
  relativ zum Speicherort der JSON, prüft sie und benennt die Dateien um.
  Das Skript ist optimiert auf die Bearbeitung von Exporten aus
  Cellebrite PA.

Details:
  - Für jeden Eintrag unter "Media" wird die Datei aus "RelativeFilePath"
    gesucht.
  - Ist die Datei kein gültiges Bild/Video oder hat ein unbekanntes Format,
    wird sie gelöscht.
  - Ist die Datei gültig, wird sie in den "FileName" aus "MediaFiles[0]"
    umbenannt.
  - Falls der gewünschte Zielname bereits existiert, wird "_<Nummer>"
    vor der Dateiendung angehängt (z.B. foo_1.mp4, foo_2.mp4, ...).

Optionen:
  -h, --help    Diese Hilfe anzeigen
  -p            Paket-Abhängigkeiten prüfen
""")

def check_dependencies():
    """
    Prüft, ob alle erforderlichen Pakete installiert sind.
    Gibt True zurück wenn alles OK, sonst False.
    """
    missing = []
    
    # Pillow prüfen
    try:
        import PIL
        print(f"✓ Pillow {PIL.__version__} ist installiert")
    except ImportError:
        missing.append("pillow")
        print("✗ Pillow fehlt")
    
    # OpenCV prüfen
    try:
        import cv2
        print(f"✓ OpenCV {cv2.__version__} ist installiert")
    except ImportError:
        missing.append("opencv-python")
        print("✗ OpenCV fehlt")
    
    if missing:
        print("\nFehlende Pakete installieren:")
        print("\nMit pip:")
        print(f"  pip install {' '.join(missing)}")
        print("\nMit MacPorts (Python 3.124 als Beispiel):")
        for pkg in missing:
            if pkg == "pillow":
                print("  sudo port install py314-pillow")
            elif pkg == "opencv-python":
                print("  sudo port install py314-opencv4")
        return False
    
    print("\n✓ Alle erforderlichen Pakete sind installiert.")
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
                img.verify()  # Prüft Bild-Header[web:87]
            # Nach verify() muss Datei neu geöffnet werden für load()
            with Image.open(path) as img:
                img.load()  # Lädt tatsächlich Bilddaten[web:94]
            return True
        except Exception as e:
            print(f"  Ungültiges Bild: {e}")
            return False
    
    # Videos mit OpenCV prüfen
    elif suffix in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v']:
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                print(f"  Video kann nicht geöffnet werden")
                return False
            
            # Versuche ersten Frame zu lesen
            ret, frame = cap.read()
            cap.release()
            
            if not ret or frame is None:
                print(f"  Kein gültiger Frame lesbar")
                return False
            
            return True
        except Exception as e:
            print(f"  Ungültiges Video: {e}")
            return False
    
    # Unbekanntes Format - als gültig behandeln
    else:
        print(f"  Unbekanntes Format {suffix}, wird nicht geprüft")
        return False

def rename_media_files(json_data, base_dir):
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
                print(f"Warnung: Datei nicht gefunden: {old_path}")
                continue
            
            # Validierung: Ist die Datei ein gültiges Bild/Video?
            print(f"Prüfe: {old_path.name}")
            if not is_valid_media(old_path):
                print(f"  -> Lösche defekte Datei: {old_path}")
                old_path.unlink()  # Datei löschen
                continue
            
            # Falls gültig: umbenennen
            desired_new = old_path.with_name(file_name)
            new_path = next_free_name(desired_new)
            
            print(f"  -> Benenne um: {new_path.name}")
            old_path.rename(new_path)

def main():
    prog = Path(sys.argv[0]).name

    # Hilfe bei -? oder fehlenden Argumenten
    # Paket-Prüfung
    if len(sys.argv) == 2 and sys.argv[1] == "-p":
        check_dependencies()
        sys.exit(0)

    # Hilfe bei -h, --help oder fehlenden Argumenten
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print_help(prog)
        sys.exit(0)

    if len(sys.argv) != 2:
        print_help(prog)
        sys.exit(1)
    
    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"Fehler: JSON-Datei nicht gefunden: {json_path}")
        sys.exit(1)
    
    base_dir = json_path.parent.resolve()
    
    loaded_data = load_json(json_path)
    rename_media_files(loaded_data, base_dir)
    
    print("\nFertig!")

if __name__ == "__main__":
    main()
