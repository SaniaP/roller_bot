import sqlite3, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = sqlite3.connect("roller_school.db")
cur = con.cursor()
# Возвращаем telegram_id главному тренеру (id=58)
cur.execute(
    "update users set telegram_id=?, role='coach', is_approved=1 where id=58 and telegram_id is null",
    (6186258951,),
)
print("rows updated:", cur.rowcount)
con.commit()
for r in cur.execute(
    "select id, telegram_id, role, full_name, is_approved from users where id=58"
):
    print(r)
con.close()
