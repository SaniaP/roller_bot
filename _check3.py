import sqlite3, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
con = sqlite3.connect("roller_school.db")
cur = con.cursor()
print("user 58 full:")
for r in cur.execute(
    "select id, telegram_id, role, full_name, is_approved, is_solo from users where id=58"
):
    print(r)
print("anyone with chief telegram_id:")
for r in cur.execute(
    "select id, telegram_id, role, full_name from users where telegram_id=6186258951"
):
    print(r)
print("users with null telegram_id:")
for r in cur.execute(
    "select id, telegram_id, role, full_name from users where telegram_id is null"
):
    print(r)
con.close()
