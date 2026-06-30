import sqlite3

conn = sqlite3.connect('database.db')
c = conn.cursor()

try:
    c.execute("ALTER TABLE books ADD COLUMN image_url TEXT;")
    print("✅ 'image_url' sütunu başarıyla eklendi!")
except sqlite3.OperationalError:
    print("⚠️ 'image_url' sütunu zaten var.")
    
conn.commit()
conn.close()
