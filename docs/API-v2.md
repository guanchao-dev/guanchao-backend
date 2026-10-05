# 观潮接口文档 v2 —— 众筹成就

> 本文只讲这一版**新增/变化**的部分。通用约定（信封结构、身份、错误码）见 `docs/API.md`。
>
> 一句话：新增 **13 个众筹成就**（共 18 个）；其中 **12 个服务端自动判定，前端一行不用改**；
> 只有 1 个需要前端多调一个上报接口。

## 前端待办（三件事）

| # | 做什么 | 在哪 | 详 |
|---|---|---|---|
| 1 | 接**上报接口**（不接的话「深蓝百万里」成就永远解锁不了） | 首页点「深蓝百万里」活动卡时 | §二 |
| 2 | 修**锁定态图标顺序**（现在锁着的勋章也露图案） | `utils/medals.ts` 的 `decorateMedal` | §五.1 |
| 3 | 修**「解锁奖励」字段**（现在显示 ★ x0 / ◆ x1） | `utils/medals.ts` 的 `buildMedalDetail` + `medal-detail.wxml` | §五.2 |

其余 12 个成就**全部由服务端自动判定**，前端不用做任何事（见 §四）。

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

**在哪调**：首页 `pages/home/home.ts` 的 `onActivity()` —— 点「深蓝百万里」那张活动卡时
（按标题匹配」深蓝百万里」），和跳第三方小程序的 `wx.navigateToMiniProgram` 放在一起。
拿到 `unlockedMedalIds` 后走首页**现成的** `enqueueUnlocks` + `flushUnlocks(this)` 弹解锁动画
（首页已经有 `<unlock-popup id="unlockPopup">`，`utils/unlock.ts` 里也有现成实现）。

**先上报再跳转**，但别等它返回 —— 跳转不依赖上报结果；未登录会 401，静默忽略即可。

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

## 五、前端要改的几处（已定位到文件）

> 下面 1、2 两条是**当前代码里实际存在的 bug**，不是理论风险 ——
> 我按你们重构后的结构（`utils/medals.ts` + `medal-wall` / `medal-detail`）定位好了文件和行号。

### 1. `utils/medals.ts` — 锁定的勋章会露出图案

`decorateMedal` 里现在是：

```js
icon: row.icon || row.iconUrl || (locked ? LOCK_ICON : MEDAL_ICONS[index % MEDAL_ICONS.length]),   // ❌
```

`locked` 排在**第三位**。后端这轮给 13 个众筹成就配了 `iconUrl`（占位图），
`||` 在这里直接短路返回 —— **没解锁的勋章也显示图案**，锁状态就没了。
（老 5 个勋章 `iconUrl` 是空串，所以以前没暴露。）

改成**先判锁定**：

```js
icon: locked ? LOCK_ICON : (row.icon || row.iconUrl || MEDAL_ICONS[index % MEDAL_ICONS.length]),   // ✅
```

`decorateMedal` 被 `pages/achieve` 和 `pages/medal-wall` 共用，改一处两个页面都好。

### 2. 「解锁奖励」读的是后端不存在的字段

`utils/medals.ts` 的 `buildMedalDetail` 里：

```js
starReward: rewards.star || 0,      // ❌ 后端没有 rewards.star
shellReward: rewards.shell || 1     // ❌ 后端没有 rewards.shell
```

后端 `rewards` 就是 `{"score": N}`，所以现在渲染出来的是兜底值 ——
`pages/medal-detail/medal-detail.wxml` 那块显示的是「★ x0」「◆ x1」。

改成只展示经验：

```js
exp: rewards.score || 0
```

wxml 对应改成单个「经验 +{{medal.exp}}」（没有经验值时整块不显示）。
老的 `pages/achieve` 页我看已经没这块了，只有 `medal-detail` 要改。

### 3. `rewards` / `description` 只有详情接口有

- `GET /medals`（列表）只返回 `id / title / rarity / iconUrl / locked / unlockedAt`
- `GET /medals/{id}`（详情）才有 `displayTitle / description / requirements / rewards`

所以经验值**只能在详情页显示**，列表拿不到。

### 4. 墙上用 `title`、详情用 `displayTitle`

两者刻意不一样：`title` 是短名（墙上显示），`displayTitle` 是完整/带玩梗的名（详情和分享用）。

| 成就 | `title`（墙） | `displayTitle`（详情） |
|---|---|---|
| “海景房” | 海景房 | “海景房” |
| 这就是…海洋记录员？ | 海洋记录员 | 这就是…海洋记录员？ |
| siuuuuuu～～～ | siuuuuuu | siuuuuuu～～～ |

### 5. 锁定的勋章，点进详情会看到真实名称

墙上锁着的显示 `???`，但**点进详情会显示真实的 `displayTitle` 和 `description`**
（"告诉你怎么解锁"）。这是原有 5 个勋章就有的设计，没动。要连详情也遮住的话需要另说。

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
