total_required_xp = 0
for lvl in range(1, 81):
    if lvl > 1:
        total_required_xp += (lvl - 1) * 200
    print(lvl,total_required_xp)