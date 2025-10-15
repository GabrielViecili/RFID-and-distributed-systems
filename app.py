from flask import Flask, request, jsonify
import sqlite3

from pubsub import AsyncConn

app = Flask(__name__)
pubnub = AsyncConn("Flask Application", "meu_canal")

# Função para conectar ao banco de dados SQLite
def connect_db():
    return sqlite3.connect('data.db')

# Função para criar a tabela se não existir
def create_table():
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    conn.close()

# Inicializa a tabela ao iniciar o aplicativo
create_table()

@app.route('/', methods=['POST', 'GET'])
def use_api():
    try:
        # ====== POST ==========================================================================
        if request.method == "POST":
            data = request.json.get('data')  # Recebe o valor do corpo da requisição JSON
        
            if data is None:
                return jsonify({"error": "No value provided"}), 400
        
            with connect_db() as conn:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO data (value) VALUES (?)', (data,))
                conn.commit()

            pubnub.publish({"text": data})
            
            return jsonify({"message": "Value added successfully"}), 201
        # ====== GET==========================================================================
        elif request.method == "GET":
            with connect_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM data')
                rows = cursor.fetchall()

            values = [{"id": row[0], "data": row[1]} for row in rows]

            return jsonify(values), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
