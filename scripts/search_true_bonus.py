base_top8 = sum([49, 36, 41, 45, 34, 32, 49, 48]) / 8
print(base_top8, "base_top8")
clamp = (base_top8 - 30) / 12
print(clamp, "clamp")
level_bonus = 1.2 * clamp
print(level_bonus, "level_bonus")
