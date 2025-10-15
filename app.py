# access_api.py (trecho principal)
from flask import Flask, request, jsonify, g
import sqlite3, hashlib, secrets, datetime, functools
from pubsub import AsyncConn  # seu arquivo pubsub.py
import os

DB_PATH = os.getenv("DB_PATH", "data.db")
app = Flask(__name__)
pub = AsyncConn("Access API", "meu_canal")

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

# util: hash password
def hash_pw(pw: str):
    return hashlib.sha256(pw.encode()).hexdigest()

# util: simple token creation
def create_token(username):
    token = secrets.token_urlsafe(32)
    expires = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    db = get_db()
    db.execute("INSERT INTO api_tokens (token, username, expires_at) VALUES (?, ?, ?)",
               (token, username, expires))
    db.commit()
    return token

def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return jsonify({"error":"missing token"}), 401
        db = get_db()
        row = db.execute("SELECT username, expires_at FROM api_tokens WHERE token = ?", (token,)).fetchone()
        if not row: return jsonify({"error":"invalid token"}), 403
        # check expiry
        if datetime.datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.datetime.utcnow():
            return jsonify({"error":"token expired"}), 403
        return fn(*args, **kwargs)
    return wrapper

# Auth login
@app.route("/auth/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username")
    pw = data.get("password")
    if not username or not pw:
        return jsonify({"error":"missing"}), 400
    db = get_db()
    user = db.execute("SELECT username, password_hash FROM collaborators WHERE username = ?", (username,)).fetchone()
    if not user or user["password_hash"] != hash_pw(pw):
        return jsonify({"error":"invalid credentials"}), 403
    token = create_token(username)
    return jsonify({"token": token}), 200

# Create collaborator
@app.route("/collaborators", methods=["POST"])
@require_auth
def create_collaborator():
    d = request.json
    db = get_db()
    db.execute("INSERT INTO collaborators (badge_id,name,role,permission_level,username,password_hash) VALUES (?,?,?,?,?,?)",
               (d["badge_id"], d["name"], d.get("role",""), d.get("permission_level",1), d.get("username"), hash_pw(d.get("password","1234"))))
    db.commit()
    return jsonify({"ok":True}), 201

# List collaborators
@app.route("/collaborators", methods=["GET"])
@require_auth
def list_collaborators():
    db = get_db()
    rows = db.execute("SELECT id,badge_id,name,role,permission_level,username FROM collaborators").fetchall()
    return jsonify([dict(r) for r in rows]), 200

# Logs endpoints
@app.route("/logs", methods=["POST"])
def push_log():
    # NOTE: logs can be posted by tag readers without auth (or with token) — adapt conforme necessidade
    d = request.json
    badge = d.get("badge_id")
    event = d.get("event_type")
    result = d.get("result")
    reason = d.get("reason", "")
    db = get_db()
    db.execute("INSERT INTO access_logs (badge_id,event_type,result,reason) VALUES (?,?,?,?)",
               (badge,event,result,reason))
    db.commit()
    # publish via pubnub
    pub.publish({"badge_id":badge,"event_type":event,"result":result,"reason":reason,"ts":str(datetime.datetime.utcnow())})
    return jsonify({"ok":True}), 201

@app.route("/logs", methods=["GET"])
@require_auth
def get_logs():
    start = request.args.get("start")
    end = request.args.get("end")
    q = "SELECT * FROM access_logs WHERE 1=1 "
    params=[]
    if start:
        q += " AND timestamp >= ? "; params.append(start)
    if end:
        q += " AND timestamp <= ? "; params.append(end)
    db = get_db()
    rows = db.execute(q, params).fetchall()
    return jsonify([dict(r) for r in rows])
