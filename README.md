# SIMP offline geoguesser

Update:
- You no longer upload a "global map" at the start.
- Each **round** only needs **one image**: the overall map for that round.
- Host flow: add players → add round (upload map) → set answer → play.

## Run (Windows PowerShell)

```powershell
py -m pip install flask pillow
py app.py
```

Open: http://127.0.0.1:5000
