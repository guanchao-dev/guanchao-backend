# 观潮接口文档 v2 —— 众筹成就

> 本文只讲这一版**新增/变化**的部分。通用约定（信封结构、身份、错误码）见 `docs/API.md`。
>
> 一句话：新增 **13 个众筹成就**（共 18 个）；其中 **12 个服务端自动判定，前端一行不用改**；
> 只有 1 个需要前端多调一个上报接口。

---

## 一、13 个新成就

| id | 成就名 | 稀有度 | 经验 | 怎么拿到 | 占位图 |
|---|---|---|---|---|---|
| `medal_6` | 蓝色青年行动 | common | 30 | **10.7–10.17 期间登录**（北京时间，含首尾） | crab-heart |
| `medal_7` | 深蓝百万里 | common | 30 | **前端上报**（见第二节） | crab-map |
| `medal_8` | ……好吧也比没有强 | common | 30 | 拍照识别出「石头」这类非生物 | crab-dig |
| `medal_9` | 蟹蟹！ | common | 40 | 识别到螃蟹（第 1 次） | crab-cloud |
| `medal_10` | 蟹老板 | rare | 80 | 识别到螃蟹累计 **10** 次 | crab-crown |
| `medal_11` | “海景房” | epic | 120 | **单次**识别出 3 个及以上物体 | crab-star |
| `medal_12` | 海星拾趣 | rare | 60 | 识别到海星 | crab-heart |
| `medal_13` | 这就是…海洋记录员？ | epic | 150 | 累计识别到 **10** 种不同生物 | crab-book |
| `medal_14` | 深蓝小卫士 | rare | 80 | 垃圾识别且 AI 判定**确实是垃圾** | crab-helmet |
| `medal_15` | 海的味道我知道！ | rare | 60 | 识别到紫菜 | crab-checklist |
| `medal_16` | siuuuuuu～～～ | epic | 150 | 图鉴集齐**所有以「螺」结尾的生物** | crab-astronaut |
| `medal_17` | 为什么我的洞口常含盐巴 | rare | 60 | 识别到蛏子 | crab-dig |
| `medal_18` | 拍我干什么？ | rare | 60 | 识别到藤壶 | crab-search |

经验梯度是 **30 / 40 / 60 / 80 / 120 / 150** 六档（白送 → 一次动作 → 定向寻找 → 要攒 → 苛刻 → 集齐）。

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
- `achievements/overview` 的 `medalTotal` = `18`，`total`（可得分总数）= `1320`
- ⚠️ `percent` / `levelProgress` 的分母变成了 1310，**所有老用户的百分比会相应下降**（他们没解锁新成就）。这是预期的，不是 bug

#### `GET /medals/{id}`

`rewards` 字段现在就是 `{"score": N}`，**N 就是经验值** ——
「解锁奖励」那块直接展示它即可（现在是 `经验 +N`）。
以前那张设计稿上的 ★ 和 ◆ 两种货币后端从来没有过，已废弃。

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

## 五、接入时踩过的坑

### 1. 勋章现在**有图了**，锁定态要优先判

这 13 个成就的 `iconUrl` 都是非空的（占位图）。原来的图标回退如果写成：

```js
icon: item.iconUrl || (locked ? '锁图' : '图案')   // ❌
```

`||` 会把锁短路掉 —— **没解锁的成就也会露出图案**，锁状态就没了（实际踩过）。
必须**先判锁定**：

```js
icon: locked ? '锁图' : (item.iconUrl || '按顺序取的兜底图')   // ✅
```

### 2. `rewards` / `description` 只有详情接口有

- `GET /medals`（列表）只返回 `id / title / rarity / iconUrl / locked / unlockedAt`
- `GET /medals/{id}`（详情）才有 `displayTitle / description / requirements / rewards`

所以「解锁奖励」（经验值 = `rewards.score`）**只能在详情弹窗里显示**，列表拿不到。

### 3. 墙上用 `title`、详情用 `displayTitle`

两者刻意不一样：`title` 是短名（墙上显示），`displayTitle` 是完整/带玩梗的名（详情和分享用）。

| 成就 | `title`（墙） | `displayTitle`（详情） |
|---|---|---|
| “海景房” | 海景房 | “海景房” |
| 这就是…海洋记录员？ | 海洋记录员 | 这就是…海洋记录员？ |
| siuuuuuu～～～ | siuuuuuu | siuuuuuu～～～ |

### 4. 锁定的勋章，点进详情会看到真实名称

墙上锁着的显示 `???`，但**点进详情会显示真实的 `displayTitle` 和 `description`**
（"告诉你怎么解锁"）。这是原有 5 个勋章就有的设计，这次没动。
如果要连详情也遮住，说一声。

---

## 六、我们替你定的两个数（设计表里留空/含糊）

| 成就 | 表里写的 | 我们暂定 | 改哪 |
|---|---|---|---|
| 蟹老板 | 「捡到很多只螃蟹」 | 识别到螃蟹 **10** 次 | `achievements.py::CRAB_TARGET` |
| 这就是…海洋记录员？ | 「累计识别（）种生物」 | 累计 **10** 种 | `achievements.py::SPECIES_TARGET` |

**经验值做过一次重新平衡**：原先 13 个挤在 40/70/120 三档，问题比较大
（白送的「登录」「点链接」和真干活的「第一次捡到螃蟹」一样多；最难的「集齐螺」和
靠运气的「海景房」都是 120；海星/紫菜/蛏子/藤壶/蟹老板/垃圾 6 个全挤在 70）。
现在改成 **30 / 40 / 60 / 80 / 120 / 150** 六档，按「越难越稀有」铺开。
要再调只改 `seed.py` 的 `rewards.score` —— `_sync_medal_fields` 会在启动时同步到线上。

另外两点确认过的处理方式：

- **「用盐抓到蛏子」**：服务端验证不了「用盐」这个物理动作，**降级为「识别到蛏子」**。
  如果要做成真的要用户标记「这次是用盐抓的」，需要再加一个上报接口。
- **「拍照识别海洋垃圾」**：只有 AI **判定确实是垃圾**才算（对着鼠标拍照返回的不是垃圾，
  不计入）。判定发生在服务端，不依赖前端上报。
