from flask import Flask, render_template, request, jsonify, session
import requests
import dropbox
from dropbox.exceptions import AuthError
import threading
import os
import json
import time
import uuid
from urllib.parse import quote

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pikdrop-secret-2026")

# ── In-memory job store (single worker only) ────────────────────────────────
jobs = {}  # job_id -> { status, progress, total, done, errors, log }

# ── PikPak constants ─────────────────────────────────────────────────────────
PIKPAK_CLIENT_ID     = "YUMx5nI8ZU8Ap8pm"
PIKPAK_CLIENT_SECRET = "dbw2OAv3zbb7TavDEsGSA7uTany"
PIKPAK_AUTH_URL      = "https://user.mypikpak.com/v1/auth/signin"
PIKPAK_API_BASE      = "https://api-drive.mypikpak.com"

# ────────────────────────────────────────────────────────────────────────────
# PikPak helpers
# ────────────────────────────────────────────────────────────────────────────

def pikpak_login(username: str, password: str):
    resp = requests.post(PIKPAK_AUTH_URL, json={
        "client_id":     PIKPAK_CLIENT_ID,
        "client_secret": PIKPAK_CLIENT_SECRET,
        "username":      username,
        "password":      password,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("access_token"), data.get("sub")

def pikpak_list_files(access_token: str, parent_id: str = "", page_token: str = ""):
    headers = {"Authorization": f"Bearer {access_token}"}
    params  = {
        "parent_id":  parent_id or "",
        "thumbnail_size": "SIZE_MEDIUM",
        "filters":    json.dumps({"trashed": {"eq": False}}),
        "limit":      100,
    }
    if page_token:
        params["page_token"] = page_token
    resp = requests.get(
        f"{PIKPAK_API_BASE}/drive/v1/files",
        headers=headers, params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.json()

def pikpak_get_file(access_token: str, file_id: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(
        f"{PIKPAK_API_BASE}/drive/v1/files/{file_id}",
        headers=headers, timeout=30
    )
    resp.raise_for_status()
    return resp.json()

def pikpak_get_download_url(access_token: str, file_id: str):
    """Returns direct download URL for a file."""
    file_info = pikpak_get_file(access_token, file_id)
    # web_content_link is the direct download URL
    url = file_info.get("web_content_link") or \
          (file_info.get("medias") or [{}])[0].get("link", {}).get("url", "")
    return url, file_info.get("name", file_id), file_info.get("size", 0)

# ────────────────────────────────────────────────────────────────────────────
# Dropbox helpers
# ────────────────────────────────────────────────────────────────────────────

def dbx_client(token: str):
    return dropbox.Dropbox(token, timeout=900)

def upload_to_dropbox(dbx, url: str, filename: str, dest_folder: str):
    """Stream file from URL into Dropbox."""
    dest_path = f"{dest_folder.rstrip('/')}/{filename}"
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        content_length = int(r.headers.get("content-length", 0))

        if content_length and content_length > 150 * 1024 * 1024:
            # Use chunked upload for files > 150 MB
            chunk_size = 100 * 1024 * 1024  # 100 MB
            upload_session = None
            offset = 0
            for chunk in r.iter_content(chunk_size=chunk_size):
                if upload_session is None:
                    result = dbx.files_upload_session_start(chunk)
                    upload_session = result.session_id
                else:
                    cursor = dropbox.files.UploadSessionCursor(
                        session_id=upload_session, offset=offset
                    )
                    if len(chunk) < chunk_size:
                        commit = dropbox.files.CommitInfo(
                            path=dest_path,
                            mode=dropbox.files.WriteMode("overwrite")
                        )
                        dbx.files_upload_session_finish(chunk, cursor, commit)
                    else:
                        dbx.files_upload_session_append_v2(chunk, cursor)
                offset += len(chunk)
        else:
            data = r.content
            dbx.files_upload(
                data, dest_path,
                mode=dropbox.files.WriteMode("overwrite")
            )
    return dest_path

# ────────────────────────────────────────────────────────────────────────────
# Background transfer worker
# ────────────────────────────────────────────────────────────────────────────

def transfer_worker(job_id, pikpak_token, dropbox_token, file_ids, dest_folder):
    job = jobs[job_id]
    job["status"]   = "running"
    job["total"]    = len(file_ids)
    job["done"]     = 0
    job["errors"]   = []
    job["log"]      = []

    try:
        dbx = dbx_client(dropbox_token)
    except Exception as e:
        job["status"] = "failed"
        job["log"].append(f"Erro Dropbox: {e}")
        return

    for fid in file_ids:
        if job.get("cancel"):
            job["status"] = "cancelled"
            break
        try:
            job["log"].append(f"⬇ Buscando info: {fid}")
            url, name, size = pikpak_get_download_url(pikpak_token, fid)
            if not url:
                raise ValueError("URL de download vazia — verifique se o arquivo não expirou")
            size_mb = round(int(size) / 1024 / 1024, 1) if size else "?"
            job["log"].append(f"📤 Enviando {name} ({size_mb} MB) → Dropbox…")
            dest = upload_to_dropbox(dbx, url, name, dest_folder)
            job["log"].append(f"✅ Salvo em {dest}")
            job["done"] += 1
        except Exception as e:
            msg = f"❌ Erro em {fid}: {e}"
            job["errors"].append(msg)
            job["log"].append(msg)

    if job["status"] == "running":
        job["status"] = "done"

# ────────────────────────────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# -- PikPak auth --

@app.route("/api/pikpak/login", methods=["POST"])
def api_pikpak_login():
    body = request.json or {}
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "Email e senha obrigatórios"}), 400
    try:
        token, user_id = pikpak_login(username, password)
        session["pikpak_token"] = token
        return jsonify({"ok": True, "user_id": user_id})
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get("error_description", str(e))
        except Exception:
            detail = str(e)
        return jsonify({"error": detail}), 400

@app.route("/api/pikpak/set_token", methods=["POST"])
def api_pikpak_set_token():
    """Allow setting a Bearer token directly."""
    body = request.json or {}
    token = body.get("token", "").strip().lstrip("Bearer").strip()
    if not token:
        return jsonify({"error": "Token vazio"}), 400
    session["pikpak_token"] = token
    return jsonify({"ok": True})

# -- PikPak files --

@app.route("/api/pikpak/files")
def api_pikpak_files():
    token = session.get("pikpak_token") or request.args.get("token")
    if not token:
        return jsonify({"error": "Não autenticado no PikPak"}), 401
    parent_id  = request.args.get("parent_id", "")
    page_token = request.args.get("page_token", "")
    try:
        data = pikpak_list_files(token, parent_id, page_token)
        return jsonify(data)
    except requests.HTTPError as e:
        return jsonify({"error": str(e)}), 400

# -- Dropbox test --

@app.route("/api/dropbox/test", methods=["POST"])
def api_dropbox_test():
    body = request.json or {}
    token = body.get("token", "").strip()
    if not token:
        return jsonify({"error": "Token vazio"}), 400
    try:
        dbx = dbx_client(token)
        account = dbx.users_get_current_account()
        session["dropbox_token"] = token
        return jsonify({"ok": True, "name": account.name.display_name, "email": account.email})
    except AuthError as e:
        return jsonify({"error": f"Token inválido: {e}"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# -- Transfer --

@app.route("/api/transfer/start", methods=["POST"])
def api_transfer_start():
    body = request.json or {}
    pikpak_token  = session.get("pikpak_token") or body.get("pikpak_token", "")
    dropbox_token = session.get("dropbox_token") or body.get("dropbox_token", "")
    file_ids      = body.get("file_ids", [])
    dest_folder   = body.get("dest_folder", "/PikPak")

    if not pikpak_token:
        return jsonify({"error": "PikPak não autenticado"}), 401
    if not dropbox_token:
        return jsonify({"error": "Dropbox não configurado"}), 401
    if not file_ids:
        return jsonify({"error": "Nenhum arquivo selecionado"}), 400

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "pending", "total": 0, "done": 0, "errors": [], "log": []}
    t = threading.Thread(
        target=transfer_worker,
        args=(job_id, pikpak_token, dropbox_token, file_ids, dest_folder),
        daemon=True
    )
    t.start()
    return jsonify({"ok": True, "job_id": job_id})

@app.route("/api/transfer/status/<job_id>")
def api_transfer_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado"}), 404
    return jsonify(job)

@app.route("/api/transfer/cancel/<job_id>", methods=["POST"])
def api_transfer_cancel(job_id):
    job = jobs.get(job_id)
    if job:
        job["cancel"] = True
    return jsonify({"ok": True})

# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
