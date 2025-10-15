#!/usr/bin/env python3
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import time, json, os, traceback
from datetime import datetime, timedelta
import sqlite3
import threading
import requests

API_URL = os.getenv("ACCESS_API_URL", "http://192.168.0.100:5000")
API_TOKEN = os.getenv("ACCESS_API_TOKEN", "")
DB_LOCAL = "rpi_local.db"
FLUSH_INTERVAL = 20

# GPIO
LED_VERDE = 17; LED_VERMELHO = 27; BUZZER = 22
GPIO.setmode(GPIO.BCM)
GPIO.setup(LED_VERDE, GPIO.OUT)
GPIO.setup(LED_VERMELHO, GPIO.OUT)
GPIO.setup(BUZZER, GPIO.OUT)
buzzer_pwm = GPIO.PWM(BUZZER, 1000)

leitorRfid = SimpleMFRC522()
stop_event = threading.Event()
lock = threading.Lock()

# In-memory structures
colaboradores = {}
presenca_sala = {}
historico_diario = {}
tentativas_negadas = {}
tentativas_invasao = 0
eventos_log = []

# Local sqlite functions
def init_local_db():
    conn = sqlite3.connect(DB_LOCAL)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS pending_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    badge_id TEXT,
                    event_type TEXT,
                    result TEXT,
                    reason TEXT,
                    timestamp DATETIME
                  )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS collab_cache (
                    badge_id TEXT PRIMARY KEY,
                    name TEXT,
                    autorizado INTEGER
                  )""")
    conn.commit()
    conn.close()

def save_collab_cache_sqlite():
    conn = sqlite3.connect(DB_LOCAL)
    cur = conn.cursor()
    with lock:
        cur.execute("DELETE FROM collab_cache")
        for badge, v in colaboradores.items():
            cur.execute("INSERT INTO collab_cache(badge_id,name,autorizado) VALUES (?,?,?)",
                        (str(badge), v["nome"], 1 if v["autorizado"] else 0))
    conn.commit(); conn.close()

def load_collab_cache_sqlite():
    global colaboradores
    conn = sqlite3.connect(DB_LOCAL)
    cur = conn.cursor()
    cur.execute("SELECT badge_id,name,autorizado FROM collab_cache")
    rows = cur.fetchall()
    conn.close()
    if rows:
        with lock:
            colaboradores = {int(r[0]): {"nome": r[1], "autorizado": bool(r[2])} for r in rows}
        print(f"[localdb] loaded {len(colaboradores)} from local cache")

def add_pending_sqlite(log):
    conn = sqlite3.connect(DB_LOCAL)
    cur = conn.cursor()
    cur.execute("INSERT INTO pending_logs(badge_id,event_type,result,reason,timestamp) VALUES (?,?,?,?,?)",
                (log.get("badge_id"), log.get("event_type"), log.get("result"), log.get("reason"), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()

def get_pending_sqlite():
    conn = sqlite3.connect(DB_LOCAL)
    cur = conn.cursor()
    cur.execute("SELECT id,badge_id,event_type,result,reason,timestamp FROM pending_logs ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_pending_sqlite(ids):
    if not ids: return
    conn = sqlite3.connect(DB_LOCAL)
    cur = conn.cursor()
    cur.execute(f"DELETE FROM pending_logs WHERE id IN ({','.join('?' for _ in ids)})", ids)
    conn.commit(); conn.close()

# API helpers
def fetch_collaborators_from_api():
    global colaboradores
    try:
        headers = {}
        if API_TOKEN: headers["Authorization"] = API_TOKEN
        r = requests.get(f"{API_URL}/collaborators", headers=headers, timeout=5)
        if r.status_code == 200:
            arr = r.json()
            with lock:
                colaboradores = {}
                for c in arr:
                    try: badge = int(c.get("badge_id"))
                    except: badge = c.get("badge_id")
                    colaboradores[badge] = {"nome": c.get("name") or c.get("nome") or "Sem Nome", "autorizado": True if c.get("permission_level",1)>=1 else False}
            save_collab_cache_sqlite()
            print("[api] sync ok")
            return True
        else:
            print("[api] error", r.status_code, r.text)
    except Exception:
        print("[api] exception:", traceback.format_exc())
    return False

def push_log_to_api(log):
    try:
        headers = {"Content-Type":"application/json"}
        if API_TOKEN: headers["Authorization"] = API_TOKEN
        r = requests.post(f"{API_URL}/logs", json=log, headers=headers, timeout=5)
        return r.status_code in (200,201)
    except Exception:
        return False

# buzzer/led functions (same as before)
def tocar_som_autorizado():
    buzzer_pwm.start(50); buzzer_pwm.ChangeFrequency(523); time.sleep(0.15)
    buzzer_pwm.ChangeDutyCycle(0); time.sleep(0.05)
    buzzer_pwm.ChangeDutyCycle(50); buzzer_pwm.ChangeFrequency(659); time.sleep(0.15)
    buzzer_pwm.ChangeDutyCycle(0)
def tocar_som_negado():
    buzzer_pwm.start(50); buzzer_pwm.ChangeFrequency(587); time.sleep(0.2)
    buzzer_pwm.ChangeDutyCycle(0); time.sleep(0.05)
    buzzer_pwm.ChangeDutyCycle(50); buzzer_pwm.ChangeFrequency(440); time.sleep(0.3)
    buzzer_pwm.ChangeDutyCycle(0)
def tocar_alarme_invasao():
    buzzer_pwm.start(50)
    for i in range(10):
        buzzer_pwm.ChangeFrequency(800); time.sleep(0.15)
        buzzer_pwm.ChangeFrequency(400); time.sleep(0.15)
    buzzer_pwm.ChangeDutyCycle(0)
def acender_led_verde():
    GPIO.output(LED_VERDE, GPIO.HIGH); time.sleep(5); GPIO.output(LED_VERDE, GPIO.LOW)
def acender_led_vermelho():
    GPIO.output(LED_VERMELHO, GPIO.HIGH); time.sleep(5); GPIO.output(LED_VERMELHO, GPIO.LOW)
def piscar_led_vermelho():
    for _ in range(10):
        GPIO.output(LED_VERMELHO, GPIO.HIGH); time.sleep(0.3); GPIO.output(LED_VERMELHO, GPIO.LOW); time.sleep(0.3)

# event registration
def registrar_evento(tipo, tag_id, nome="Desconhecido", autorizado=None, resultado=""):
    evento = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tipo_evento": tipo, "tag_id": tag_id, "nome": nome, "autorizado": autorizado, "resultado": resultado}
    with lock:
        eventos_log.append(evento)
    log_for_api = {"badge_id": tag_id, "event_type": tipo, "result": "GRANTED" if autorizado else "DENIED", "reason": resultado}
    if not push_log_to_api(log_for_api):
        add_pending_sqlite(log_for_api)

# presence logic — same as previous script
def registrar_entrada(tag_id, nome):
    if tag_id not in presenca_sala:
        presenca_sala[tag_id] = {"dentro": False, "entrada": None, "tempo_total": timedelta(0)}
    presenca_sala[tag_id]["dentro"] = True
    presenca_sala[tag_id]["entrada"] = datetime.now()
def registrar_saida(tag_id):
    if tag_id in presenca_sala and presenca_sala[tag_id]["dentro"]:
        entrada = presenca_sala[tag_id]["entrada"]
        tempo_sessao = datetime.now() - entrada
        presenca_sala[tag_id]["tempo_total"] += tempo_sessao
        presenca_sala[tag_id]["dentro"] = False
        presenca_sala[tag_id]["entrada"] = None

def processar_acesso(tag_id):
    global tentativas_invasao
    try:
        if tag_id not in colaboradores:
            tentativas_invasao += 1
            registrar_evento("INVASAO", tag_id, "Desconhecido", False, "Tag não cadastrada")
            tocar_alarme_invasao(); piscar_led_vermelho(); return
        colaborador = colaboradores[tag_id]; nome = colaborador["nome"]; autorizado = colaborador["autorizado"]
        if not autorizado:
            tentativas_negadas[tag_id] = tentativas_negadas.get(tag_id,0)+1
            registrar_evento("ACESSO_NEGADO", tag_id, nome, False, "Colaborador sem autorização")
            tocar_som_negado(); acender_led_vermelho(); return
        if tag_id not in presenca_sala or not presenca_sala[tag_id]["dentro"]:
            primeira_vez = tag_id not in historico_diario
            if primeira_vez:
                historico_diario[tag_id] = True
                registrar_evento("ENTRADA", tag_id, nome, True, "Primeira entrada do dia")
            else:
                registrar_evento("ENTRADA", tag_id, nome, True, "Retorno à sala")
            registrar_entrada(tag_id, nome); tocar_som_autorizado(); acender_led_verde()
        else:
            tempo_sessao = datetime.now() - presenca_sala[tag_id]["entrada"]
            minutos = int(tempo_sessao.total_seconds() // 60)
            registrar_evento("SAIDA", tag_id, nome, True, f"Permaneceu {minutos} minutos")
            registrar_saida(tag_id); tocar_som_autorizado(); acender_led_verde()
    except Exception:
        print("process error", traceback.format_exc())

# flush worker (reads pending rows and attempts sending)
def flush_worker():
    while not stop_event.is_set():
        rows = get_pending_sqlite()
        if rows:
            ids_to_delete=[]
            for r in rows:
                pid, badge, event_type, result, reason, ts = r
                log = {"badge_id": badge, "event_type": event_type, "result": result, "reason": reason}
                ok = push_log_to_api(log)
                if ok:
                    ids_to_delete.append(str(pid))
            if ids_to_delete:
                delete_pending_sqlite(ids_to_delete)
        # attempt api sync for collaborators
        fetch_collaborators_from_api()
        stop_event.wait(FLUSH_INTERVAL)

# export CSV on shutdown (same idea)
def export_csv():
    if not eventos_log: return
    import csv, os
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not os.path.exists("relatorios"): os.makedirs("relatorios")
    path = f"relatorios/relatorio_acesso_{ts}.csv"
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['timestamp','tipo_evento','tag_id','nome','autorizado','resultado'])
        writer.writeheader(); writer.writerows(eventos_log)
    print("CSV salvo:", path)

def main():
    init_local_db()
    load_collab_cache_sqlite()
    fetch_collaborators_from_api()
    t = threading.Thread(target=flush_worker, daemon=True); t.start()
    try:
        tag_anterior=None; tempo_ultimo=0
        while True:
            print("Aguardando tag...")
            tag_id, text = leitorRfid.read()
            agora = time.time()
            if tag_id == tag_anterior and (agora - tempo_ultimo) < 3: continue
            tag_anterior = tag_id; tempo_ultimo = agora
            processar_acesso(tag_id)
            time.sleep(1)
    except KeyboardInterrupt:
        print("Encerrando...")
    finally:
        stop_event.set(); export_csv()
        GPIO.cleanup()

if __name__ == "__main__":
    main()
