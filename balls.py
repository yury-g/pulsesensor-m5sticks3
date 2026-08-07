import M5
import time

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
BG = 0x000000
lcd.fillScreen(BG)

W, H = lcd.width(), lcd.height()   # 240 x 135
R = 11                             # ball radius

balls = [
    {"x": 50.0,  "y": 40.0, "vx": 3.1,  "vy": 2.0,  "c": 0xFF4040},  # red
    {"x": 120.0, "y": 90.0, "vx": -2.4, "vy": 2.8,  "c": 0x40FF40},  # green
    {"x": 190.0, "y": 50.0, "vx": 2.0,  "vy": -3.2, "c": 0x40A0FF},  # blue
]

def step():
    # move + wall bounce
    for b in balls:
        b["x"] += b["vx"]
        b["y"] += b["vy"]
        if b["x"] < R:      b["x"] = R;      b["vx"] = abs(b["vx"])
        if b["x"] > W - R:  b["x"] = W - R;  b["vx"] = -abs(b["vx"])
        if b["y"] < R:      b["y"] = R;      b["vy"] = abs(b["vy"])
        if b["y"] > H - R:  b["y"] = H - R;  b["vy"] = -abs(b["vy"])
    # ball-ball elastic collisions (equal mass)
    n = len(balls)
    for i in range(n):
        for j in range(i + 1, n):
            a, c = balls[i], balls[j]
            dx = c["x"] - a["x"]
            dy = c["y"] - a["y"]
            d2 = dx * dx + dy * dy
            min_d = 2 * R
            if d2 < min_d * min_d and d2 > 0:
                d = d2 ** 0.5
                nx, ny = dx / d, dy / d
                # push apart so they don't stick
                overlap = (min_d - d) / 2
                a["x"] -= nx * overlap; a["y"] -= ny * overlap
                c["x"] += nx * overlap; c["y"] += ny * overlap
                # exchange velocity along the collision normal
                va = a["vx"] * nx + a["vy"] * ny
                vc = c["vx"] * nx + c["vy"] * ny
                a["vx"] += (vc - va) * nx; a["vy"] += (vc - va) * ny
                c["vx"] += (va - vc) * nx; c["vy"] += (va - vc) * ny

print("balls running")
while True:
    old = [(int(b["x"]), int(b["y"])) for b in balls]
    step()
    for (ox, oy), b in zip(old, balls):
        lcd.fillCircle(ox, oy, R, BG)                          # erase old
    for b in balls:
        lcd.fillCircle(int(b["x"]), int(b["y"]), R, b["c"])    # draw new
    time.sleep_ms(16)
