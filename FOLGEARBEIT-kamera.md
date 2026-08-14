# Offene Folgearbeit aus dem Branch feature/kamera-erkennung-belichtung

Bewusst nicht in diesem Branch behoben. Nach Aufwand/Nutzen sortiert.

## Auf dem Pi zu bestätigen (nicht ohne Hardware prüfbar)
- Auflösungsliste im Dashboard gegen `v4l2-ctl --list-formats-ext -d /dev/video0` vergleichen.
- Intervall-Aufnahme über einen Sonnenaufgang laufen lassen und prüfen, ob `exposure_auto = 3`
  die Automatik vor jedem Warm-up wirklich neu scharfstellt.
- Auto-Belichtungs-Schalter im Dashboard an echter UVC-Hardware prüfen (Anzeige und Schreibwert).
- Während laufender Aufnahme "Kameras suchen" klicken: es darf kein Frame ausfallen.
- Session compilen und auf Helligkeitssprünge achten.

## Verhaltensänderung, die auffallen kann
- Clip-Modus läuft bei aktiver Ziel-Helligkeit jetzt vollständig mit Auto-Belichtung.
  Vorher wurde eine gespeicherte manuelle Belichtung berücksichtigt. `capture_clip` ruft
  `_capture_balanced` nicht auf. Für eine Aufnahme über die Dämmerung ist das vermutlich besser,
  aber es ist eine Änderung.
- `loadFps` fällt nach einem Netzwerkfehler nicht mehr auf 5–30 fps zurück, sondern behält die
  vorhandene Liste. Da das Markup nur "10 fps" vorbelegt, kann der Fallback-Zweig nie feuern.
- Eine leere Erkennung wird nicht mehr gecacht, also bei jedem Aufruf neu versucht. Bei einer
  wirklich toten Kamera kostet das jedes Mal bis zu 5 s v4l2-ctl-Timeout statt nur einmal.

## Spec-Abweichung
- Spec 5.4 verlangt HTTP 409 auf den Erkennungs-Endpunkten bei belegter Kamera. Die vier
  `except CameraBusy`-Handler in `api/timelapse.py` sind unerreichbar: die OpenCV-Fallbacks geben
  bei Lock-Timeout `[]` zurück statt zu werfen, und `detect_formats` nimmt gar keinen Lock.
  Auf dem Pi mit v4l2-ctl wird der Pfad nie erreicht, weil die Formatabfrage keinen Lock braucht.

## Design
- Auflösungen werden nicht nach Pixelformat gefiltert. Eine C920 bietet 1920x1080 auch an, wenn
  YUYV gewählt ist — der Treiber snapped dann stumm auf 640x480. Spec 5.3 schreibt die
  Deduplizierung über alle Formate ausdrücklich vor, die Kombination bleibt aber unvalidiert.
- `services/camera.py` ist auf über 800 Zeilen gewachsen und enthält fünf Kopien von
  "Capture öffnen" mit drei verschiedenen Fehlerverträgen. Die Erkennungsmethoden sind zustandslos
  und gehören in ein eigenes Modul mit einem gemeinsamen `_open_probe()`. Vor den beiden anderen
  Plänen einplanen, die weiteren Code in diese Datei legen.

## Kleinigkeiten
- `_open_capture` liest `self._camera_index` zweimal; ein Settings-PUT dazwischen sperrt Gerät A
  und öffnet Gerät B.
- `clear_detect_cache` wird nur von Tests aufgerufen.
- `capture_preview` nutzt `_capture_balanced` nicht, die Vorschau zeigt also nicht, was gespeichert
  würde.
- Veralteter Geräteindex führt zu HTTP 422 und einer leeren Auflösungsliste.
- `detect_properties` gibt `[]` statt einer Ausnahme zurück, wenn die Kamera nicht öffnet, und ruft
  `cap.release()` außerhalb von `finally`.
- Ein nicht-numerischer Settings-Wert lässt die Timelapse-Schleife dauerhaft im 60-s-Retry hängen.
