import sys, io, sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

con = sqlite3.connect("roller_school.db")
cur = con.cursor()
cur.execute(
    "INSERT OR IGNORE INTO users (id, telegram_id, role, full_name, is_approved, level, xp, title, is_solo) "
    "VALUES (58, 6186258951, 'coach', 'Главный Тренер', 1, 1, 0, 'Новичок', 0)"
)
con.commit()
print(
    "restored:",
    cur.execute(
        "select id, telegram_id, role, full_name from users where id=58"
    ).fetchall(),
)
