#!/usr/bin/env python3
import sqlite3
import pandas as pd
from datetime import datetime

DB = "data.db"  # path to API sqlite; se usar outra localização, ajuste

def load_logs(start=None, end=None):
    conn = sqlite3.connect(DB, detect_types=sqlite3.PARSE_DECLTYPES)
    q = "SELECT * FROM access_logs "
    params=[]
    if start and end:
        q += " WHERE timestamp BETWEEN ? AND ? "
        params=[start,end]
    df = pd.read_sql_query(q, conn, parse_dates=['timestamp'], params=params)
    conn.close()
    return df

def daily_counts(date_str):
    start = f"{date_str} 00:00:00"; end = f"{date_str} 23:59:59"
    df = load_logs(start,end)
    if df.empty:
        print("Nenhum log no dia", date_str); return
    counts = df.groupby(['event_type','result']).size().unstack(fill_value=0)
    print(f"Contagens para {date_str}:\n", counts)

def hours_by_collaborator(start=None,end=None):
    df = load_logs(start,end)
    if df.empty:
        print("Nenhum log no período"); return
    df = df.sort_values(['badge_id','timestamp'])
    results = {}
    for badge, g in df.groupby('badge_id'):
        entry_time = None
        total_hours = 0.0
        for _, row in g.iterrows():
            if row['event_type'] == 'ENTRADA' and row['result'] in ('GRANTED','Granted','granted',True):
                entry_time = row['timestamp']
            elif row['event_type'] in ('SAIDA','EXIT') and entry_time is not None:
                delta = pd.to_datetime(row['timestamp']) - pd.to_datetime(entry_time)
                total_hours += delta.total_seconds()/3600.0
                entry_time = None
        results[badge] = total_hours
    s = pd.Series(results).sort_values(ascending=False)
    print("Horas por colaborador (horas):\n", s)
    return s

if __name__ == "__main__":
    # exemplos
    daily_counts("2025-10-14")
    hours_by_collaborator()
