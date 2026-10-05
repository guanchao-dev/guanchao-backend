# 观潮接口文档 v2 —— 众筹成就

> 本文只讲这一版**新增/变化**的部分。通用约定（信封结构、身份、错误码）见 `docs/API.md`。
>
> 一句话：新增 **13 个众筹成就**（共 18 个）；其中 **12 个服务端自动判定，前端一行不用改**；
> 只有 1 个需要前端多调一个上报接口。

---

## 一、13 个新成就

| id | 成就名 | 稀有度 | 分值 | 怎么拿到 | 占位图 |
|---|---|---|---|---|---|
| `medal_6` | 蓝色青年行动 | common | 40 | **10.7–10.17 期间登录**（北京时间，含首尾） | crab-heart |
| `medal_7` | 深蓝百万里 | common | 40 | **前端上报**（见第二节） | crab-map |
| `medal_8` | ……好吧也比没有强 | common | 40 | 拍照识别出「石头」这类非生物 | crab-dig |
| `medal_9` | 蟹蟹！ | common | 40 | 识别到螃蟹（第 1 次） | crab-cloud |
| `medal_10` | 蟹老板 | rare | 70 | 识别到螃蟹累计 **10** 次 | crab-crown |
| `medal_11` | “海景房” | epic | 120 | **单次**识别出 3 个及以上物体 | crab-star |
| `medal_12` | 海星拾趣 | rare | 70 | 识别到海星 | crab-heart |
| `medal_13` | 这就是…海洋记录员？ | epic | 120 | 累计识别到 **10** 种不同生物 | crab-book |
| `medal_14` | 深蓝小卫士 | rare | 70 | 垃圾识别且 AI 判定**确实是垃圾** | crab-helmet |
| `medal_15` | 海的味道我知道！ | rare | 70 | 识别到紫菜 | crab-checklist |
| `medal_16` | siuuuuuu～～～ | epic | 120 | 图鉴集齐**所有以「螺」结尾的生物** | crab-astronaut |
| `medal_17` | 为什么我的洞口常含盐巴 | rare | 70 | 识别到蛏子 | crab-dig |
| `medal_18` | 拍我干什么？ | rare | 70 | 识别到藤壶 | crab-search |

- **占位图暂时用现成的 badge 图**，正式图案出来只改 `seed.py` 里那一行 `icon_key`。
- 「螺」类**按名录动态判定**（`Species.name` 以「螺」结尾，当前 5 个）；以后加物种，成就条件自动跟着变。
- 稀有度/分值是按「越难触发越稀有」给的一套默认，要调整改 `seed.py` 的 `MEDALS`。

---

## 二、前端唯一需要新增的调用

### `POST /achievements/report` — 上报一个服务端看不见的动作

「深蓝百万里」是**用户点了一个链接**，服务端看不见这件事，只能由前端上报。

**需要登录**（`Authorization: Bearer <token>`）。

```http
POST /api/v1/achievements/report
Content-Type: application/json
Authorization: Bearer <token>

{ "event": "deepblue_mileage" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `event` | ✅ | 动作名。**目前只支持 `deepblue_mileage`**（用户点进「深蓝百万里」时上报）。以后加别的动作会在这里扩 |

**响应**：

```json
{ "code": 0, "message": "ok",
  "data": { "event": "deepblue_mileage", "unlockedMedalIds": ["medal_7"] } }
```

`unlockedMedalIds` 的用法和其它接口**完全一致**：非空就弹解锁动画。

| 情况 | 结果 |
|---|---|
| 首次上报 | `unlockedMedalIds: ["medal_7"]` |
| 重复上报 | `unlockedMedalIds: []`（幂等，不重复给、不报错） |
| 未登录 | `401` |
| `event` 不认识 | `400 不支持的事件` |

> 做成白名单而不是「传什么就发什么」—— 否则任何人都能拿这个接口把勋章刷满。

**调用时机建议**：用户点「深蓝百万里」的入口时先上报再跳转（或跳转后异步上报都行），
失败不用阻塞用户。

---

## 三、已有接口的变化

### `GET /medals` / `GET /medals/{id}` / `GET /achievements/overview`

**内容变多了，字段结构没变**：

- 勋章从 5 个变成 **18 个**
- `achievements/overview` 的 `medalTotal` = `18`，`total`（可得分总数）= `1310`
- ⚠️ `percent` / `levelProgress` 的分母变成了 1310，**所有老用户的百分比会相应下降**（他们没解锁新成就）。这是预期的，不是 bug

### `POST /ai/trash-guess`

响应新增一个字段：

```json
{ "isTrash": true, "items": [...], "unlockedMedalIds": ["medal_14"] }
```

非空就弹解锁动画（和其它接口一样）。**没有垃圾时**（`isTrash: false`）这个字段是空数组。

### `POST /watch/sessions/{id}/species`

响应新增一个字段：

```json
{ "id": "wr_…", "species": [...], "newlyLitSpecies": {...}, "unlockedMedalIds": ["medal_16"] }
```

用来在「点亮图鉴」后立刻拿到新成就（集齐「螺」就是这个路径）。

### `POST /auth/wechat-login`

接口签名和响应结构**没变**，但服务端在登录时会顺手结算一次成就
（「蓝色青年行动」靠这个）。刚注册/刚登录的用户，返回的用户数据里
`score` 可能已经包含了新勋章的分。

---

## 四、不需要前端做的事

其余 **12 个成就全部由服务端自动判定**，前端**不需要额外传任何东西**：

- 识别类（蟹/海星/紫菜/蛏子/藤壶/石头/单次≥3种）：服务端读识别记录
- 累计识别物种数、集齐「螺」：服务端读识别记录 + 图鉴点亮记录
- 深蓝小卫士：服务端在垃圾识别接口里自己记一笔
- 蓝色青年行动：服务端在登录时判断日期

---

## 五、我们替你定的两个数（设计表里留空/含糊）

| 成就 | 表里写的 | 我们暂定 | 改哪 |
|---|---|---|---|
| 蟹老板 | 「捡到很多只螃蟹」 | 识别到螃蟹 **10** 次 | `achievements.py::CRAB_TARGET` |
| 这就是…海洋记录员？ | 「累计识别（）种生物」 | 累计 **10** 种 | `achievements.py::SPECIES_TARGET` |

另外两点确认过的处理方式：

- **「用盐抓到蛏子」**：服务端验证不了「用盐」这个物理动作，**降级为「识别到蛏子」**。
  如果要做成真的要用户标记「这次是用盐抓的」，需要再加一个上报接口。
- **「拍照识别海洋垃圾」**：只有 AI **判定确实是垃圾**才算（对着鼠标拍照返回的不是垃圾，
  不计入）。判定发生在服务端，不依赖前端上报。
