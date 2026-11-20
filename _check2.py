import sqlite3, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = sqlite3.connect("roller_school.db")
for r in con.execute(
    "select id, telegram_id, role, full_name, is_approved, is_solo from users where id >= 55"
):
    print(r)
