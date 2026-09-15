"""在服务器上连测同一张图 3 次，看识别是否稳定、图鉴匹配是否正确。"""
import httpx

c = httpx.Client(base_url="https://www.blueakaiwu.cn/api/v1", timeout=60, verify=False)
H = {"X-Client-Id": "verify_stable_1"}

IMGS = [
    ("许氏平鲉鱼苗", "data/uploads/pictorial/sp_korean_rockfish.jpg"),
    ("海星", "data/uploads/pictorial/sp_starfish.jpg"),
    ("藤壶", "data/uploads/pictorial/sp_barnacle.jpg"),
]

for label, path in IMGS:
    with open(path, "rb") as f:
        img = f.read()
    print("=== %s ===" % label)
    for i in range(3):
        uid = c.post("/uploads", headers=H, data={"scene": "speciesGuess"},
                     files={"file": ("x.jpg", img, "image/jpeg")}).json()["data"]["uploadId"]
        d = c.post("/ai/species-guess", headers=H,
                   json={"uploadId": uid, "spotId": "spot_qd_shilaoren"}).json()["data"]
        out = " | ".join(
            "%s %s%s" % (x["probabilityText"], x["name"], "(图鉴)" if x["inEncyclopedia"] else "")
            for x in d.get("candidates", [])
        )
        print("  第%d次: %s" % (i + 1, out))
