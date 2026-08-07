import M5
import time
import random

M5.begin()
lcd = M5.Lcd
lcd.setRotation(1)
BG = 0x000000
lcd.fillScreen(BG)

W, H = lcd.width(), lcd.height()   # 240 x 135
R = 11
MAX_BALLS = 10
COLORS = [0xFF4040, 0x40FF40, 0x40A0FF, 0xFFFF40, 0xFF40FF, 0x40FFFF,
          0xFF8000, 0x80FF80, 0x8080FF, 0xFFFFFF]

def new_ball():
    a = random.getrandbits(8)
    return {"x": float(random.randint(R, W - R)), "y": float(random.randint(R, H - R)),
            "vx": 2.0 + (a & 3), "vy": -(2.0 + (a >> 6)),
            "c": COLORS[random.getrandbits(8) % len(COLORS)]}

balls = [new_ball() for _ in range(3)]

def show_count():
    lcd.setTextColor(0xA0A0A0, BG)
    lcd.setTextSize(2)
    lcd.setCursor(4, 4)
    lcd.print("%d " % len(balls))

def step():
    for b in balls:
        b["x"] += b["vx"]; b["y"] += b["vy"]
        if b["x"] < R:      b["x"] = R;      b["vx"] = abs(b["vx"])
        if b["x"] > W - R:  b["x"] = W - R;  b["vx"] = -abs(b["vx"])
        if b["y"] < R:      b["y"] = R;      b["vy"] = abs(b["vy"])
        if b["y"] > H - R:  b["y"] = H - R;  b["vy"] = -abs(b["vy"])
    n = len(balls)
    for i in range(n):
        for j in range(i + 1, n):
            a, c = balls[i], balls[j]
            dx = c["x"] - a["x"]; dy = c["y"] - a["y"]
            d2 = dx * dx + dy * dy
            min_d = 2 * R
            if 0 < d2 < min_d * min_d:
                d = d2 ** 0.5
                nx, ny = dx / d, dy / d
                ov = (min_d - d) / 2
                a["x"] -= nx * ov; a["y"] -= ny * ov
                c["x"] += nx * ov; c["y"] += ny * ov
                va = a["vx"] * nx + a["vy"] * ny
                vc = c["vx"] * nx + c["vy"] * ny
                a["vx"] += (vc - va) * nx; a["vy"] += (vc - va) * ny
                c["vx"] += (va - vc) * nx; c["vy"] += (va - vc) * ny

print("balls_buttons running: BtnA=add ball, BtnB=remove ball")
show_count()
while True:
    M5.update()
    if M5.BtnA.wasPressed() and len(balls) < MAX_BALLS:
        balls.append(new_ball())
        show_count()
        print("added ball ->", len(balls))
    if M5.BtnB.wasPressed() and len(balls) > 1:
        b = balls.pop()
        lcd.fillCircle(int(b["x"]), int(b["y"]), R, BG)
        show_count()
        print("removed ball ->", len(balls))
    old = [(int(b["x"]), int(b["y"])) for b in balls]
    step()
    for (ox, oy), b in zip(old, balls):
        lcd.fillCircle(ox, oy, R, BG)
    for b in balls:
        lcd.fillCircle(int(b["x"]), int(b["y"]), R, b["c"])
    time.sleep_ms(16)
