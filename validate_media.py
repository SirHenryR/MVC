#!/usr/bin/env python3

import json
import sys
import shutil
import logging
import time
import subprocess
import shutil as _shutil
import json as _json
from pathlib import Path
from multiprocessing import Pool, cpu_count, TimeoutError as MPTimeoutError
from typing import Optional, Tuple, List

# Konfiguration
MEDIA_CHECK_TIMEOUT = 10.0  # Sekunden Timeout pro Datei
LOG_ENABLED = False         # wird in main() durch -l gesetzt
MAX_WORKERS = 4             # maximale Anzahl Worker-Prozesse (0/None = alle CPUs)

# HEIF/HEIC-Unterstützung registrieren (falls installiert)
HEIC_SUPPORTED = False
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------


def setup_logging(log_file: Path) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
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
# ffprobe-Hilfsfunktionen
# ----------------------------------------------------------------------


def has_ffprobe() -> bool:
    """Prüft, ob ffprobe im PATH verfügbar ist."""
    return _shutil.which("ffprobe") is not None


def is_valid_video_ffprobe(path: Path, timeout: float = 10.0) -> Optional[bool]:
    """
    Prüft mit ffprobe, ob die Datei ein lesbares Video mit mind. einem Videostream enthält.
    True = ffprobe findet Videostream, Rückgabecode 0
    False = ffprobe-Fehler, kein Videostream oder Auswertungsfehler
    None = ffprobe nicht verfügbar oder Timeout
    """
    if not has_ffprobe():
        log_print(" ffprobe nicht gefunden (nicht im PATH)")
        return None

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-select_streams",
        "v:0",
        "-print_format",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log_print(f" ffprobe-Timeout nach {timeout:.1f}s")
        return None
    except Exception as e:
        log_print(f" ffprobe-Aufruf fehlgeschlagen: {e}")
        return False

    if result.returncode != 0:
        err = result.stderr.strip()
        if err:
            log_print(f" ffprobe-Fehler: {err}")
        return False

    try:
        data = _json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            log_print(" ffprobe: kein Videostream gefunden")
            return False
        return True
    except Exception as e:
        log_print(f" ffprobe-Output nicht lesbar: {e}")
        return False


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------


def check_dependencies() -> bool:
    """
    Prüft, ob Pillow, ffprobe und optional HEIC-Unterstützung installiert sind.
    """
    missing = []

    # Pillow
    try:
        import PIL  # noqa
        log_print("✓ Pillow ist installiert")
    except ImportError:
        missing.append("pillow")
        log_print("✗ Pillow fehlt")

    # ffprobe (Teil von ffmpeg)
    if has_ffprobe():
        log_print("✓ ffprobe ist im PATH verfügbar")
    else:
        log_print("✗ ffprobe fehlt oder ist nicht im PATH")
        log_print(" Hinweis: ffprobe ist Teil von ffmpeg. Installation z.B.:")
        log_print(" - Debian/Ubuntu: sudo apt install ffmpeg")
        log_print(" - macOS (brew): brew install ffmpeg")
        log_print(" - Windows (choco): choco install ffmpeg")

    # HEIC-Unterstützung
    if HEIC_SUPPORTED:
        log_print("✓ HEIC-Unterstützung (pillow-heif) ist aktiviert")
    else:
        log_print("! HEIC-Unterstützung nicht aktiv (pillow-heif nicht installiert?)")
        log_print(
            " Hinweis: HEIC-Dateien werden nur als gültig erkannt, wenn pillow-heif verfügbar ist."
        )
        log_print(" Installation z.B.: pip install pillow-heif")

    if missing or not has_ffprobe():
        if missing:
            log_print("\nFehlende Python-Pakete installieren, z.B.:")
            log_print(f" pip install {' '.join(missing)}")
        if not has_ffprobe():
            log_print("\nffprobe (ffmpeg) installieren und im PATH verfügbar machen.")
        return False

    log_print("✓ Alle erforderlichen Basispakete sind installiert")
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


def _calc_workers() -> int:
    if MAX_WORKERS and MAX_WORKERS > 0:
        return min(cpu_count(), MAX_WORKERS)
    return cpu_count()


# ----------------------------------------------------------------------
# Dateien inhaltlich als Bild erkennen
# ----------------------------------------------------------------------


def detect_image_format(path: Path) -> Optional[str]:
    """
    Versucht, das Bildformat einer Datei per Pillow zu erkennen.
    Gibt z.B. 'JPEG', 'PNG', 'TIFF' oder None bei Fehler zurück.
    """
    try:
        from PIL import Image, ImageFile

        ImageFile.LOAD_TRUNCATED_IMAGES = True
        with Image.open(path) as img:
            return img.format  # z.B. 'JPEG', 'PNG', 'TIFF'
    except Exception:
        return None


def image_format_to_suffix(fmt: str) -> Optional[str]:
    """
    Mappt Pillow-Formate auf Dateiendungen.
    """
    fmt = (fmt or "").upper()
    mapping = {
        "JPEG": ".jpg",
        "JPG": ".jpg",
        "PNG": ".png",
        "TIFF": ".tif",
        "BMP": ".bmp",
        "GIF": ".gif",
        "WEBP": ".webp",
        "HEIC": ".heic",
    }
    return mapping.get(fmt)


# ----------------------------------------------------------------------
# Vor-Normalisierung: Bild/Video erkennen und Extension anpassen
# ----------------------------------------------------------------------


def detect_media_and_normalize_suffix(path: Path) -> Optional[Path]:
    """
    Prüft, ob Datei Bild oder Video ist und passt ggf. die Extension an.
    - Wenn Bild: immer per Inhalt erkannt, Extension auf echtes Format normalisieren.
    - Wenn Video: nur per bekannter Video-Extension + ffprobe.
    Gibt den (evtl. umbenannten) Pfad zurück oder None, wenn es kein Bild/Video ist.
    """
    suffix = path.suffix.lower()
    stem = path.stem

    # 1) Versuche immer zuerst, ob es sich inhaltlich um ein Bild handelt
    fmt = detect_image_format(path)
    if fmt:
        new_ext = image_format_to_suffix(fmt)
        if not new_ext:
            # Unbekanntes Bildformat, aber als Bild erkannt -> nichts ändern
            return path

        old_ext_clean = suffix.lstrip(".") if suffix else "NOEXT"
        # Nur umbenennen, wenn alte Extension von neuer abweicht
        if suffix != new_ext:
            new_name = f"({old_ext_clean}){stem}{new_ext}"
            new_path = next_free_name(path.with_name(new_name))
            log_print(
                f" -> Extension-Korrektur (Bild, inhaltlich erkannt): "
                f"{path.name} -> {new_path.name}"
            )
            path.rename(new_path)
            return new_path
        return path  # schon passende Extension

    # 2) Wenn kein Bild: prüfen, ob es ein Video mit bekannter Endung ist
    known_video_suffixes = [
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".flv",
        ".wmv",
        ".webm",
        ".m4v",
        ".hevc",
        ".h265",
    ]

    if suffix in known_video_suffixes:
        ok = is_valid_video_ffprobe(path, timeout=MEDIA_CHECK_TIMEOUT)
        if not ok:
            return None
        # Keine Extension-Umbenennung für Videos nötig, daher Pfad zurückgeben
        return path

    # Weder als Bild erkennbar noch Video mit bekannter Extension
    return None



# ----------------------------------------------------------------------
# Medienprüfung (im Worker, ohne weiteren Process)
# ----------------------------------------------------------------------


def _check_media_worker(path: Path) -> bool:
    """
    Läuft im Worker-Prozess.
    Gibt True (gültig) oder False (ungültig) zurück.
    Keine Timeouts hier; Timeout wird im Hauptprozess gehandhabt.
    """
    suffix = path.suffix.lower()
    try:
        # Bildformate (inkl. HEIC, wenn unterstützt)
        known_image_suffixes = [
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
            ".tif",
        ]
        if HEIC_SUPPORTED:
            known_image_suffixes.append(".heic")

        # Dateien mit bekannter Bild-Endung
        if suffix in known_image_suffixes:
            from PIL import Image, ImageFile

            ImageFile.LOAD_TRUNCATED_IMAGES = True
            with Image.open(path) as img:
                img.load()
            return True

        # Videoformate (inkl. rohe HEVC-Streams)
        known_video_suffixes = [
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".flv",
            ".wmv",
            ".webm",
            ".m4v",
            ".hevc",
            ".h265",
        ]
        if suffix in known_video_suffixes:
            ok = is_valid_video_ffprobe(path, timeout=10.0)
            return bool(ok)

        # Alles andere (inkl. Dateien ohne Extension):
        # Versuch, ob es sich inhaltlich um ein Bild handelt.
        fmt = detect_image_format(path)
        if fmt:
            return True

        # unbekanntes Format -> ungültig
        return False
    except Exception as e:
        log_print(f" Medienprüfung fehlgeschlagen: {e}")
        return False


def is_valid_media(path: Path, timeout: float, pool: Pool) -> Optional[bool]:
    """
    Prüft mit Timeout im Hauptprozess, ob Datei ein gültiges Bild/Video ist.
    Rückgabewerte:
    True = gültig
    False = ungültig
    None = Prüfung abgebrochen (Timeout oder interner Fehler)
    """
    async_result = pool.apply_async(_check_media_worker, (path,))
    try:
        result = async_result.get(timeout=timeout)
    except MPTimeoutError:
        log_print(f" Prüfung abgebrochen (Timeout nach {timeout:.1f}s)")
        result = None
    except Exception:
        log_print(" Prüfung abgebrochen (Fehler im Worker)")
        result = None
    return result


# ----------------------------------------------------------------------
# Hauptlogik: JSON-basierte Verarbeitung (parallel + Timeout)
# ----------------------------------------------------------------------


def rename_media_files(json_data, base_dir: Path, move_mode: bool = False) -> None:
    """
    Verarbeitet Mediendateien aus der ProjectVic-JSON.

    move_mode=False:
        - gültige Dateien werden im Medienordner umbenannt
        - ungültige Dateien werden gelöscht
        - Dateien mit Timeout werden nach base_dir/timeout/ verschoben

    move_mode=True:
        - gültige Dateien werden nach base_dir/valid/ verschoben (mit neuem Namen)
        - ungültige Dateien werden nach base_dir/invalid/ verschoben
        - Dateien mit Timeout werden nach base_dir/timeout/ verschoben
    """
    timeout_dir = base_dir / "timeout"
    timeout_dir.mkdir(exist_ok=True)

    if move_mode:
        valid_dir = base_dir / "valid"
        invalid_dir = base_dir / "invalid"
        valid_dir.mkdir(exist_ok=True)
        invalid_dir.mkdir(exist_ok=True)
        log_print("Move-Modus aktiv")
        log_print(f" Valid: {valid_dir}")
        log_print(f" Invalid: {invalid_dir}")
        log_print(f" Timeout: {timeout_dir}")
    else:
        log_print(f"Timeout-Verzeichnis: {timeout_dir}")

    # 1. Alle relevanten Dateien aus der JSON einsammeln
    tasks: List[Tuple[Path, str]] = []

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

            tasks.append((old_path, file_name))

    log_print(f"Zu prüfende Dateien (aus JSON): {len(tasks)}")

    valid_count = 0
    invalid_count = 0
    skipped_timeout = 0

    if not tasks:
        log_print("\n=== Statistik ===")
        log_print(f"Gültige Dateien: {valid_count}")
        log_print(f"Ungültige Dateien: {invalid_count}")
        log_print(f"Mit Timeout verschoben: {skipped_timeout}")
        log_print(f"Gesamt (bewertet): {valid_count + invalid_count}")
        return

    workers = _calc_workers()
    log_print(f"Starte Prüfungen mit {workers} Worker-Prozess(en)")

    # 2. Pool erstellen und pro Datei mit Timeout prüfen
    with Pool(processes=workers) as pool:
        for old_path, file_name in tasks:
            # Vor-Normalisierung: Bild/Video erkennen, Extension ggf. korrigieren
            log_print(f"\nPrüfe (Vor-Normalisierung): {old_path}")
            norm_path = detect_media_and_normalize_suffix(old_path)

            if norm_path is None:
                # Kein Bild/Video -> wie ungültig behandeln
                log_print(" -> Keine gültige Bild-/Videodatei (Vorprüfung)")
                invalid_count += 1
                if move_mode:
                    dest = next_free_name((base_dir / "invalid") / old_path.name)
                    log_print(f" -> Verschiebe nach invalid/: {dest.name}")
                    try:
                        shutil.move(str(old_path), str(dest))
                        log_print(" -> Erfolgreich verschoben")
                    except Exception as e:
                        log_print(f" -> Fehler beim Verschieben: {e}")
                else:
                    log_print(f" -> Lösche Datei: {old_path}")
                    try:
                        old_path.unlink()
                        log_print(" -> Erfolgreich gelöscht")
                    except Exception as e:
                        log_print(f" -> Fehler beim Löschen: {e}")
                continue

            old_path = norm_path

            log_print(f"\nPrüfe (Hauptprüfung): {old_path}")
            check_result = is_valid_media(old_path, MEDIA_CHECK_TIMEOUT, pool)

            # None = Timeout / Fehler -> Datei nach timeout/ verschieben
            if check_result is None:
                skipped_timeout += 1
                dest = next_free_name(timeout_dir / old_path.name)
                log_print(" -> Prüfung ohne Ergebnis (Timeout/Fehler)")
                log_print(f" -> Verschiebe nach timeout/: {dest.name}")
                try:
                    shutil.move(str(old_path), str(dest))
                    log_print(" -> Erfolgreich verschoben")
                except Exception as e:
                    log_print(f" -> Fehler beim Verschieben: {e}")
                continue

            # Ungültig (False)
            if not check_result:
                invalid_count += 1
                if move_mode:
                    dest = next_free_name((base_dir / "invalid") / old_path.name)
                    log_print(f" -> Verschiebe nach invalid/: {dest.name}")
                    try:
                        shutil.move(str(old_path), str(dest))
                        log_print(" -> Erfolgreich verschoben")
                    except Exception as e:
                        log_print(f" -> Fehler beim Verschieben: {e}")
                else:
                    log_print(f" -> Lösche Datei: {old_path}")
                    try:
                        old_path.unlink()
                        log_print(" -> Erfolgreich gelöscht")
                    except Exception as e:
                        log_print(f" -> Fehler beim Löschen: {e}")
                continue

            # Gültig (True)
            valid_count += 1

            suffix = old_path.suffix.lower()
            target_name = file_name

            # Dateien ohne Extension -> (NOEXT)NAME.ext (mit Fallback)
            if suffix == "":
                fmt = detect_image_format(old_path)
                ext = image_format_to_suffix(fmt)
                if not ext:
                    # Fallback, falls das Format nicht gemappt werden kann:
                    ext = ".jpg"
                base_stem = Path(file_name).stem
                target_name = f"(NOEXT){base_stem}{ext}"
            else:
                # Einordnung in bekannte Typen
                known_image_suffixes = [
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".gif",
                    ".bmp",
                    ".tiff",
                    ".webp",
                    ".tif",
                ]
                if HEIC_SUPPORTED:
                    known_image_suffixes.append(".heic")
                known_video_suffixes = [
                    ".mp4",
                    ".avi",
                    ".mov",
                    ".mkv",
                    ".flv",
                    ".wmv",
                    ".webm",
                    ".m4v",
                    ".hevc",
                    ".h265",
                ]

                # Dateien mit unbekannter Extension, aber Bild -> (ALTEXT)NAME.ext2
                if suffix not in known_image_suffixes and suffix not in known_video_suffixes:
                    fmt = detect_image_format(old_path)
                    ext2 = image_format_to_suffix(fmt)
                    if ext2:
                        base_stem = Path(file_name).stem
                        old_ext_clean = suffix.lstrip(".") if suffix else "NOEXT"
                        target_name = f"({old_ext_clean}){base_stem}{ext2}"

            if move_mode:
                desired_dest = (base_dir / "valid") / target_name
                dest = next_free_name(desired_dest)
                log_print(f" -> Verschiebe nach valid/: {dest.name}")
                try:
                    shutil.move(str(old_path), str(dest))
                    log_print(" -> Erfolgreich verschoben")
                except Exception as e:
                    log_print(f" -> Fehler beim Verschieben: {e}")
            else:
                desired_new = old_path.with_name(target_name)
                new_path = next_free_name(desired_new)
                log_print(f" -> Benenne um: {new_path.name}")
                old_path.rename(new_path)

    log_print("\n=== Statistik ===")
    log_print(f"Gültige Dateien: {valid_count}")
    log_print(f"Ungültige Dateien: {invalid_count}")
    log_print(f"Mit Timeout verschoben: {skipped_timeout}")
    log_print(f"Gesamt (bewertet): {valid_count + invalid_count}")



# ----------------------------------------------------------------------
# Cleanup-Modus (rekursiv, unabhängig von JSON, parallel + Timeout)
# ----------------------------------------------------------------------


def cleanup_directory(directory: Path) -> None:
    """
    Durchsucht ein Verzeichnis rekursiv und löscht alle ungültigen Mediendateien.
    Dateien mit Timeout werden nach /timeout/ verschoben.
    """
    log_print(f"\nBereinige Verzeichnis: {directory}")
    timeout_dir = directory / "timeout"
    timeout_dir.mkdir(exist_ok=True)
    log_print(f"Timeout-Verzeichnis: {timeout_dir}")

    all_files: List[Path] = [p for p in directory.rglob("*") if p.is_file()]
    log_print(f"Zu prüfende Dateien (Cleanup): {len(all_files)}")

    deleted_count = 0
    skipped_timeout = 0

    if not all_files:
        log_print(f"\n{deleted_count} ungültige Datei(en) gelöscht.")
        return

    workers = _calc_workers()
    log_print(f"Starte Cleanup-Prüfungen mit {workers} Worker-Prozess(en)")

    with Pool(processes=workers) as pool:
        for file_path in all_files:
            # Vor-Normalisierung
            log_print(f"\nPrüfe (Vor-Normalisierung): {file_path}")
            norm_path = detect_media_and_normalize_suffix(file_path)

            if norm_path is None:
                log_print(" -> Keine gültige Bild-/Videodatei (Vorprüfung)")
                log_print(f" -> Lösche ungültige Datei: {file_path}")
                try:
                    file_path.unlink()
                    deleted_count += 1
                    log_print(" -> Erfolgreich gelöscht")
                except Exception as e:
                    log_print(f" -> Fehler beim Löschen: {e}")
                continue

            file_path = norm_path

            log_print(f"\nPrüfe (Hauptprüfung): {file_path}")
            check_result = is_valid_media(file_path, MEDIA_CHECK_TIMEOUT, pool)

            if check_result is None:
                skipped_timeout += 1
                log_print(" -> Prüfung ohne Ergebnis (Timeout/Fehler)")
                dest = next_free_name(timeout_dir / file_path.name)
                log_print(f" -> Verschiebe nach timeout/: {dest.name}")
                try:
                    shutil.move(str(file_path), str(dest))
                    log_print(" -> Erfolgreich verschoben")
                except Exception as e:
                    log_print(f" -> Fehler beim Verschieben: {e}")
                continue

            if not check_result:
                log_print(f" -> Lösche ungültige Datei: {file_path}")
                try:
                    file_path.unlink()
                    deleted_count += 1
                    log_print(" -> Erfolgreich gelöscht")
                except Exception as e:
                    log_print(f" -> Fehler beim Löschen: {e}")

    log_print(f"\n{deleted_count} ungültige Datei(en) gelöscht.")
    if skipped_timeout:
        log_print(
            f"{skipped_timeout} Datei(en) wegen Timeout/Fehler nach timeout/ verschoben."
        )


# ----------------------------------------------------------------------
# CLI / main
# ----------------------------------------------------------------------


def print_help(prog: str) -> None:
    print(
        f"""Verwendung:

{prog}
    Standard: Gültige Dateien umbenennen, ungültige löschen.

{prog} -m <json>
    Move-Modus: Gültige nach ./valid/, ungültige nach ./invalid/ verschieben.

{prog} -c <verzeichnis>
    Cleanup-Modus: Verzeichnis rekursiv prüfen, ungültige Dateien löschen.

{prog} -p
    Paket-Abhängigkeiten (Pillow, ffprobe) prüfen.

Optionen:
    -l
        Optional: Logging in Logdatei aktivieren.
        Bei JSON: .log
        Bei -c: _cleanup.log
        Bei -p: dependency_check.log

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
    - Falls der gewünschte Zielname bereits existiert, wird "_"
      vor der Dateiendung angehängt (z.B. foo_1.mp4, foo_2.mp4, ...).

Sonderfälle:
    - Dateien ohne Extension:
      Werden mit Pillow auf Bildtauglichkeit geprüft; bei Erfolg
      in (NOEXT)NAME.EXT umbenannt (EXT je nach erkanntem Format).

    - Dateien mit unbekannter Extension:
      Wenn sie inhaltlich ein Bild sind, werden sie in
      (ALTEXT)NAME.EXT2 umbenannt, wobei ALTEXT die alte Extension
      (ohne Punkt) und EXT2 der erkannte Bildtyp ist.

Optionen:
    -h, --help   Diese Hilfe anzeigen
    -p           Paket-Abhängigkeiten prüfen
    -c           Alle ungültigen Dateien in einem Verzeichnis löschen
                 (Rekursiv! Keine Warnung! Verzeichnis prüfen!)
    -m           Move-Modus: Verschiebt Dateien nach valid/ oder invalid/
                 statt zu löschen/umzubenennen
    -l           (optional) Logging nach .log (andernfalls nur Ausgabe)

Hinweise:
    - Die Medienprüfung hat einen Timeout von {MEDIA_CHECK_TIMEOUT:.1f}s pro Datei.
    - Bei Timeout/Fehler wird die Datei nicht verändert, sondern nach timeout/ verschoben.
    - Für Videodateien wird ffprobe (Teil von ffmpeg) verwendet. ffprobe muss im PATH
      verfügbar sein, sonst werden Videos als ungültig behandelt.
"""
    )


def main() -> None:
    global LOG_ENABLED

    prog = Path(sys.argv[0]).name
    args = sys.argv[1:]
    start_time = time.time()

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
        log_print("Starte Paketprüfung (mit Logdatei)" if LOG_ENABLED else "Starte Paketprüfung")
        ok = check_dependencies()
        elapsed = time.time() - start_time
        log_print(f"Gesamtlaufzeit: {elapsed:.2f} Sekunden")
        sys.exit(0 if ok else 1)

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
        elapsed = time.time() - start_time
        log_print(f"Gesamtlaufzeit: {elapsed:.2f} Sekunden")
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

    elapsed = time.time() - start_time
    log_print(f"Gesamtlaufzeit: {elapsed:.2f} Sekunden")
    log_print("Fertig!")


if __name__ == "__main__":
    main()
