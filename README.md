# LDT ⇄ IES Toolkit

Flask app: view EULUMDAT (.ldt), convert to full-beam / IES (with selectable
C-plane rotation), compare against a reference, room heat map.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                      # http://127.0.0.1:5000/
```

Optional env vars: `HOST`, `PORT`, `FLASK_DEBUG=0`.
To test the sub-folder behaviour locally: `APP_BASE_PATH=/ldt python app.py`
then open http://127.0.0.1:5000/ldt/

## Deploy on the server (cPanel → Setup Python App)

1. Python version **3.9 or newer**.
2. Application root = the folder that contains `passenger_wsgi.py`
   (keep it outside `public_html`).
3. Application URL = your sub-folder (e.g. `yourdomain.com/ldt`).
4. Application startup file = `passenger_wsgi.py`, entry point = `application`.
5. Upload/extract this project into the application root.
6. In the same page: *Configuration files* → add `requirements.txt` → **Run Pip Install**.
7. **Restart** (or `touch tmp/restart.txt`).

Uploaded files are stored in `uploads/` (override with the `LDT_UPLOAD_DIR`
environment variable). The folder must be writable by the app user.

If the sub-folder is NOT detected automatically (links/API calls hit the
domain root), set `APP_BASE_PATH=/ldt` — either as an environment variable in
Setup Python App, or in a `.env` file in the app root (copy `.env.example`).

Other servers: `gunicorn wsgi:application`.

## Troubleshooting

- 500 / "Incomplete response" → check `passenger_error.log` and `stderr.log`.
- `ModuleNotFoundError: pyldt` → step 6 wasn't run for this app's virtualenv
  (the package is `eulumdat-py`).
