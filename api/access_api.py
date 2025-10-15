#!/usr/bin/env python3
"""
Access Management API (Flask) using SQLite and PubNub.
Endpoints:
 - POST /auth/login            -> login (username/password) -> creates token
 - POST /collaborators         -> create collaborator (auth)
 - GET  /collaborators         -> list collaborators (auth)
 - GET  /collaborators/<id>    -> get collaborator (auth)
 - PUT  /collaborators/<id>    -> update (auth)
 - DELETE /collaborators/<id>  -> delete (auth)
 - POST /logs                  -> receive access log (from RPi or other)
 - GET  /logs                  -> list logs (auth + filters start/end)
"""
import os
import sqlite3
import hashlib
import secrets
import datetime
import functools
from flask import Flask, request, jsonify, g
from pathlib import Path
import json

# PubNub publisher helper (assumes you have a pubsub.py file that provides publish function)
# If your pubsub.py exports a class or helper, adapt import below.
try:
    from pubsub import AsyncConn
    PUB = AsyncConn("AccessAPI", "access_channel")
except Exception:
    PUB = None

DB_PATH = os.getenv("DB_PATH", "data.db")
if not Path(DB_PATH).exists():
    print("DB not found - creating and running migrations...")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    # run migration SQL
    with open(Path(__file__).parent / "migrations" / "schema.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(sql)
    conn.commit()
    conn.close()

app = Flask(__name__)

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def hash_pw(pw: str):
    return hashlib.sha256(pw.encode()).hexdigest()

def create_token(username):
    token = secrets.token_urlsafe(32)
    expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
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
        if not row:
            return jsonify({"error":"invalid token"}), 403
        if datetime.datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.datetime.utcnow():
            return jsonify({"error":"token expired"}), 403
        return fn(*args, **kwargs)
    return wrapper

@app.route("/auth/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username")
    pw = data.get("password")
    if not username or not pw:
        return jsonify({"error":"missing username/password"}), 400
    db = get_db()
    user = db.execute("SELECT username, password_hash FROM collaborators WHERE username = ?", (username,)).fetchone()
    if not user or user["password_hash"] != hash_pw(pw):
        return jsonify({"error":"invalid credentials"}), 403
    token = create_token(username)
    return jsonify({"token": token}), 200

@app.route("/collaborators", methods=["POST"])
@require_auth
def create_collaborator():
    d = request.json or {}
    required = ["badge_id", "name", "username", "password"]
    for k in required:
        if k not in d:
            return jsonify({"error":f"missing {k}"}), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO collaborators (badge_id,name,role,permission_level,username,password_hash) VALUES (?,?,?,?,?,?)",
            (d["badge_id"], d["name"], d.get("role",""), d.get("permission_level",1), d["username"], hash_pw(d["password"]))
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        return jsonify({"error":"integrity", "msg": str(e)}), 400
    return jsonify({"ok":True}), 201

@app.route("/collaborators", methods=["GET"])
@require_auth
def list_collaborators():
    db = get_db()
    rows = db.execute("SELECT id,badge_id,name,role,permission_level,username FROM collaborators").fetchall()
    return jsonify([dict(r) for r in rows]), 200

@app.route("/collaborators/<int:cid>", methods=["GET"])
@require_auth
def get_collaborator(cid):
    db = get_db()
    row = db.execute("SELECT id,badge_id,name,role,permission_level,username FROM collaborators WHERE id = ?", (cid,)).fetchone()
    if not row: return jsonify({"error":"not found"}), 404
    return jsonify(dict(row)), 200

@app.route("/collaborators/<int:cid>", methods=["PUT"])
@require_auth
def update_collaborator(cid):
    d = request.json or {}
    allowed = ["name","role","permission_level","username","password","badge_id"]
    sets=[]
    params=[]
    if not d:
        return jsonify({"error":"missing body"}), 400
    for k in allowed:
        if k in d:
            if k=="password":
                sets.append("password_hash = ?")
                params.append(hash_pw(d[k]))
            else:
                sets.append(f"{k} = ?")
                params.append(d[k])
    if not sets:
        return jsonify({"error":"nothing to update"}), 400
    params.append(cid)
    db = get_db()
    db.execute(f"UPDATE collaborators SET {', '.join(sets)} WHERE id = ?", params)
    db.commit()
    return jsonify({"ok":True}), 200

@app.route("/collaborators/<int:cid>", methods=["DELETE"])
@require_auth
def delete_collaborator(cid):
    db = get_db()
    db.execute("DELETE FROM collaborators WHERE id = ?", (cid,))
    db.commit()
    return jsonify({"ok":True}), 200

@app.route("/logs", methods=["POST"])
def push_log():
    d = request.json or {}
    badge = d.get("badge_id")
    event = d.get("event_type")
    result = d.get("result")
    reason = d.get("reason", "")
    db = get_db()
    db.execute("INSERT INTO access_logs (badge_id,event_type,result,reason,timestamp) VALUES (?,?,?,?,?)",
               (badge,event,result,reason, datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    db.commit()
    payload = {"badge_id":badge,"event_type":event,"result":result,"reason":reason,"ts":datetime.datetime.utcnow().isoformat()}
    # publish via PubNub
    try:
        if PUB:
            PUB.publish(payload)
    except Exception:
        pass
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
