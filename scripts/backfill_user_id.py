"""Asigna todos los análisis con user_id=NULL al único usuario existente."""

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("ERROR: DATABASE_URL no configurada")
    sys.exit(1)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

# Obtener el único usuario
cur.execute("SELECT id, email FROM users ORDER BY id LIMIT 1")
row = cur.fetchone()
if not row:
    print("No hay usuarios en la DB.")
    conn.close()
    sys.exit(1)

user_id, email = row
print(f"Usuario: id={user_id}, email={email}")

# Contar huérfanos
cur.execute("SELECT count(*) FROM analyses WHERE user_id IS NULL")
null_count = cur.fetchone()[0]
print(f"Análisis sin user_id: {null_count}")

if null_count == 0:
    print("Nada que actualizar.")
    conn.close()
    sys.exit(0)

# Backfill
cur.execute("UPDATE analyses SET user_id = %s WHERE user_id IS NULL", (user_id,))
conn.commit()
print(f"Actualizados: {cur.rowcount} análisis → user_id={user_id}")

conn.close()
