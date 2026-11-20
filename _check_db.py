import sqlite3, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = sqlite3.connect(r"c:\MyPhytonProjects\roller_bot\roller_school.db")
cur = con.cursor()

print("USERS:")
for r in cur.execute(
    "select id, telegram_id, role, full_name, is_approved, is_solo from users"
):
    print(r)

print("\nCOACH_STUDENT:")
for r in cur.execute("select * from coach_student"):
    print(r)

print("\nCOMMUNITIES:")
try:
    for r in cur.execute("select * from communities"):
        print(r)
except Exception as e:
    print("err:", e)

print("\nCOMMUNITY_COACH:")
try:
    for r in cur.execute("select * from community_coach"):
        print(r)
except Exception as e:
    print("err:", e)

con.close()
