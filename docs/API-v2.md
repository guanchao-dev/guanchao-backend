# 观潮接口文档 v2 —— 众筹成就

> 本文只讲这一版**新增/变化**的部分。通用约定（信封结构、身份、错误码）见 `docs/API.md`。
>
> 一句话：新增 **13 个众筹成就**（共 18 个）；其中 **12 个服务端自动判定，前端一行不用改**；
> 只有 1 个需要前端多调一个上报接口。

## 前端待办（四件事）

| # | 做什么 | 在哪 | 详 |
|---|---|---|---|
| 1 | 接**上报接口**（不接的话「深蓝百万里」成就永远解锁不了） | 首页点「深蓝百万里」活动卡时 | §二 |
| 2 | 修**锁定态图标顺序**（现在锁着的勋章也露图案） | `utils/medals.ts` 的 `decorateMedal` | §五.1 |
| 3 | 修**「解锁奖励」字段**（现在显示 ★ x0 / ◆ x1） | `utils/medals.ts` 的 `buildMedalDetail` + `medal-detail.wxml` | §五.2 |
| 4 | 删掉内置的**红石崖**兜底条目（ID 写错，导致列表里有两个红石崖；且它坐标在海里） | `utils/spotGuide.ts` 的 `FALLBACK_SPOTS` | §七 |

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
- ⚠️ `percent` 的分母（`total`）现在是 **1320**，**所有老用户的百分比会相应下降**
  （他们没解锁新成就）。这是预期的，不是 bug
- `levelProgress` 在 v2 后半程改成了**本级进度**，不再是这个百分比 —— 见 §八
- 每个接口「发什么 / 返什么」的完整清单见 §九

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

---

## 七、点位下线：42 → 38

做了一轮全量点位导航核查（对每个点位：把描述里「导航：…」的目的地拿去高德搜，
再对现有坐标做逆地理编码 + 周边 POI 落海检测），42 个点位里 **4 个坐标不可信**，
已从后端摘除 —— **现有点位 38 个**。

| 点位 id | 名称 | 为什么摘 |
|---|---|---|
| `spot_qd_17` | 青岛·红石崖 | 坐标 `36.095299,120.112572` 是拿「红石崖」这个**大地名**地理编码出来的，落在红石崖街道中心（红石崖初级中学），不是描述写的导航目的地「红石崖赶海停车场」，差 800m |
| `spot_qd_24` | 青岛·顾家岛码头 | 高德搜不到这个地名，最接近的结果是「鱼鸣嘴码头」—— 那是**另一个点位**，写进去就是错数据（原来一直是 `lat/lng = None`） |
| `spot_qd_25` | 青岛·红树林 | 坐标 `36.306133,120.30648` 落在**城阳区棘洪滩**（力鼎智能科技产业园），离声明的黄岛区 **51.9km** —— 按「红树林」字面搜到的产业园 |
| `spot_qd_39` | 青岛·韩家岭村 | 落点是 **20km 外的内陆同名村**（即墨区灵山街道韩家岭村，周边全是 204 国道/202 省道沿线的路边店）；同批其它 5 个即墨点都在鳌山卫街道海边 |

处理方式（`app/db/seed.py`）：

- 新增 `DEPRECATED_SPOTS` 常量 + `_remove_deprecated_spots()`：**启动时**把这几行的
  `spots` 记录连同 `spot_harmonics` 里的调和常数一起删掉（幂等，可重复执行）。
- 只删点位行和它的派生数据；**用户产生的卡片 / 社区笔记 / 签到 / 识别记录不动** ——
  这些地方读点位都是 `db.get(Spot, id)`，取不到会自己兜底（已逐一确认）。
- 等实地核对坐标后，把 id 从 `DEPRECATED_SPOTS` 移出、坐标补回 `SPOTS` 即可；
  调和常数用 `tools/build_spot_harmonics.py` 重跑补上。

> `GET /spots`、`GET /spots/{id}` 等接口**签名和结构都没变**，只是总数少 4 个、
> 分页里不再出现这 4 个 id。前端不依赖具体点位数量，无需改动。

### 前端要删的东西

**删掉 `utils/spotGuide.ts` 里 `FALLBACK_SPOTS` 中那条内置红石崖** —— 把**整个对象**删掉，
不是改坐标：

```ts
// utils/spotGuide.ts —— FALLBACK_SPOTS 目前只有这一个元素，整条删掉后就是空数组
const FALLBACK_SPOTS: GuideSpot[] = [
  {
    id: 'spot_qd_hongshiya',
    name: '红石崖',
    // ……
    latitude: 36.1085,     // ← 这个点在胶州湾水里
    longitude: 120.2355,
    navName: '红石崖赶海停车场',
    // ……
  }
]
// 删完：const FALLBACK_SPOTS: GuideSpot[] = []
// withFallbackSpots() 传空数组照常工作，只是不再往列表尾巴上追加任何兜底点位。
```

两个**互相独立**的问题，都不改坐标、直接删：

1. **id 跟后端对不上**。后端这个点的 id 是 `spot_qd_17`，兜底写的是 `spot_qd_hongshiya`。
   `withFallbackSpots()` 是**按 id 合并**的（`FALLBACK_SPOTS.filter(s => !apiIds[s.id])`），
   所以兜底那条**永远不会被后端覆盖**，会一直作为**第二个「红石崖」**追加在列表里。
2. **它的坐标在胶州湾水里**。`36.1085,120.2355` 逆地理编码返回「中华人民共和国」（无任何地址），
   周边 500m 内 **0 个 POI** —— 点它导航就是**导到海里**。

后端删掉 `spot_qd_17` 之后，这条兜底会变成**唯一还叫「红石崖」的条目**，问题只会更显眼。
红石崖以后恢复收录时后端会自己返回，不需要前端兜底。

---

## 八、用户等级

新增**用户等级**。等级由「成就值」和「去过的赶海点位」推出来，`GET /auth/me` 和
`GET /achievements/overview` 都会返回 —— 前端现有的 `Lv.{level} {title}` 直接就能用，
不接新接口也能看到等级动起来。

### 经验怎么算

```
xp = 成就值(user.score) + 去过的点位数 × 60
```

- **成就值**：已解锁勋章的 `rewards.score` 累计（18 枚全拿到是 **1320**）。
- **去过的点位**：用户的**观潮记录**（`watch_records`）与**打卡记录**（`checkins`）里
  出现过的不同 `spot_id` 的**并集**。同一个点只算一次，重复去不再加。
- **每去一个新点位 +60 经验**：和一枚**稀有**勋章同档。实地跑一个点位是这个产品最重的
  行为，值一枚稀有勋章；但整个成就系统（18 枚合计）也才 1320，再高就把成就压没了，
  所以定 **60**。

### 等级门槛（累计经验）

| 等级 | 累计经验 | 到下一级还需 |
|---|---|---|
| Lv.1 | 0 | 60 |
| Lv.2 | 60 | 90 |
| Lv.3 | 150 | 150 |
| Lv.4 | 300 | 200 |
| Lv.5 | 500 | 300 |
| Lv.6 | 800 | 400 |
| Lv.7 | 1200 | 500 |
| Lv.8 | 1700 | 600 |
| Lv.9 | 2300 | 700 |
| Lv.10 | 3000 | ——（封顶） |

满经验 ≈ **3600**（1320 成就值 + 38 个点位 × 60），所以 Lv.10 只有「集齐勋章 + 跑遍点位」到得了。
门槛在 `app/services/levels.py` 的 `LEVEL_THRESHOLDS`，要调只改这一行；每点经验是
`XP_PER_SPOT`。

### 接口变化

`GET /auth/me` —— 新增这些字段（`level` / `title` / `score` 本来就有）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `level` | int | 等级。**以前恒为 1，现在是真的了** |
| `title` | string | 称号。仍是 `users.title`，本轮没动 |
| `xp` | int | 经验 = `score + visitedSpotCount × 60` |
| `xpToNext` | int | 距下一级还差多少经验；满级为 0 |
| `levelProgress` | int | **当前等级内**的进度百分比 0–99；满级恒 100 |
| `visitedSpotCount` | int | 去过的赶海点位数 |

`GET /achievements/overview` —— 同样新增 `xp` / `xpToNext` / `visitedSpotCount`，
`level` 变成真等级。**注意 `levelProgress` 语义变了**：以前它错放的是「勋章收集度百分比」，
现在改成**本级进度**；要勋章收集度用同一响应里的 `percent`（这个没动）。

### 什么时候刷新

等级是从上面两个数推出来的**派生值**，不单独维护（`users.level` 只在**变化时**才写库）。
自动同步的时机：

- 登录 `POST /auth/wechat-login`
- 读 `GET /auth/me`
- 新解锁勋章（成就值变了）
- 读 `GET /achievements/overview`

> 榜单和社区作者卡上的等级读的是 `users.level`，靠上面这些时机刷新 ——
> 用户下次打开「我的」页就对齐了。

### 一处依赖缺口

前端 `pages/spot-collection` 在调后端的「赶海点点亮」接口（`GET /spots/visited`、
`POST /spots/{spotId}/visit`），**但后端目前没实现这两个接口**，现在应该会 404。
等级这里的「去过点位」先用「观潮记录 ∪ 打卡记录」顶着；等点亮接口做出来，
把 `levels.visited_spot_count()` 换成读点亮表即可，其余不用动。

---

## 九、成就 / 勋章接口全集

前面各节讲的是**变化**，这一节是**全集** —— 成就相关的接口一共 **11 个**，逐个写清
「前端发什么 / 后端返什么」。（`docs/API.md` 是 v1 的，里面完全没有成就接口，只看这一份。）

**通用约定**

- 一律走统一信封：`{ "code": 0, "message": "ok", "data": {...}, "requestId": "..." }`，
  下面只写 `data` 里的内容，`code != 0` 时 `data` 为 `null` 且 `message` 是原因。
- 「登录」列 ✅ 的接口要带 `Authorization: Bearer <accessToken>`；
  没带或过期返回 `401 / code 40101 / 未登录`。「可选」表示登录与否都能调，只是返回内容不同。
- 凡是响应里出现 `unlockedMedalIds`，**非空就弹解锁动画**（全站统一的约定）。

| 接口 | 登录 | 作用 |
|---|---|---|
| `GET /achievements/overview` | 可选 | 成就总览（分数、收集度、等级） |
| `GET /achievements/pending-unlocks` | ✅ | 已解锁但还没弹过祝贺的勋章 |
| `POST /achievements/report` | ✅ | 上报服务端看不见的动作（深蓝百万里） |
| `POST /medals/{medal_id}/unlock-ack` | ✅ | 确认「祝贺弹窗已看过」 |
| `GET /medals` | 可选 | 勋章墙列表（18 枚，带锁定态） |
| `GET /medals/{medal_id}` | 可选 | 单个勋章详情（解锁条件 + 奖励） |
| `POST /medals/{medal_id}/share` | ✅ | 生成分享文案 / 跳转路径 |
| `GET /achievements/friends` | ✅ | 好友榜（首版固定只返回自己） |
| `GET /achievements/leaderboard` | 可选 | 排行榜（全站 / 好友） |
| `POST /users/{user_id}/follow` | ✅ | 关注某人 |
| `DELETE /users/{user_id}/follow` | ✅ | 取消关注 |

> 前端从 **v1.0.0（提审版）** 起，排行榜去掉了「关注 / 我的好友」整块
> （为过审做精简，见提交 `3e67cd1`）。所以 `GET /achievements/friends` 和
> `POST/DELETE /users/{user_id}/follow` **目前没有调用方** —— 后端接口保留不动，
> 以后要把社交加回来直接用。

### `GET /achievements/overview`

无 query 参数。

```json
{ "score": 120, "total": 1320, "percent": 9, "unlockedCount": 3, "medalTotal": 18,
  "level": 3, "title": "海洋探索家", "levelProgress": 60,
  "xp": 240, "xpToNext": 60, "visitedSpotCount": 2 }
```

（这个例子：成就值 120 + 去过 2 个点位 × 60 = **240 经验** → Lv.3，本级要 150→300，
所以进度 60%、还差 60。`percent` 是勋章收集度 120/1320 = 9%。）

- `score` 成就值 · `total` 可得分总数（1320）· `percent` 勋章收集度 = `score/total`
- `level` / `title` / `levelProgress` / `xp` / `xpToNext` / `visitedSpotCount`：等级相关，见 §八
- **未登录**时返回全 0 版本：`score/percent/unlockedCount/xp/visitedSpotCount = 0`、
  `level = 1`、`title = "海洋探索家"`、`levelProgress = 0`、`xpToNext = 60`

### `GET /achievements/pending-unlocks`

无参数。返回**已解锁但 `acked=false`（还没弹过祝贺）**的勋章；没有就空列表，不会 404。

```json
{ "list": [
  { "medalId": "medal_7", "title": "深蓝百万里", "displayTitle": "深蓝百万里",
    "description": "点进「深蓝百万里」看一看。",
    "iconUrl": "https://www.blueakaiwu.cn/api/v1/static/assets/badges/crab-map.png",
    "rarity": "common", "source": "report", "unlockedAt": "2026-10-06T12:00:00+08:00" } ] }
```

### `POST /achievements/report`

见 §二（`event` 目前只支持 `deepblue_mileage`）。

### `POST /medals/{medal_id}/unlock-ack`

```http
POST /api/v1/medals/medal_7/unlock-ack
{ "source": "report", "clientTime": "2026-10-06T12:00:00+08:00" }
```

两个 body 字段**都可选**：`source` 会覆盖这道勋章的解锁来源，`clientTime` 服务端不用。

```json
{ "medalId": "medal_7", "acked": true, "alreadyAcked": false, "rewards": { "score": 30 } }
```

| 情况 | 结果 |
|---|---|
| 首次确认 | `alreadyAcked: false` |
| 重复确认 | `alreadyAcked: true`（不重复发奖，也不报错） |
| 勋章 id 不存在 | `404 勋章不存在` |
| 自己没解锁这枚 | `404 尚未解锁该勋章` |

### `GET /medals`

无参数，按 `sort` 排序返回全部 18 枚，**只带墙上要用的字段**（没有 description / rewards）。

```json
{ "list": [ { "id": "medal_1", "title": "初次见面", "rarity": "common",
              "iconUrl": "", "locked": true, "unlockedAt": null } ] }
```

- `iconUrl` 是**完整 URL**（数据库里存的就是完整地址，客户端直接用，**不用再拼前缀**）；
  老 5 枚勋章的 `iconUrl` 是空串（还没配正式图案），见 §五.1
- 未登录时全部 `locked: true`

### `GET /medals/{medal_id}`

```json
{ "id": "medal_1", "title": "初次见面", "displayTitle": "初来乍到", "rarity": "common",
  "iconUrl": "", "locked": true, "description": "第一次生成属于你的图鉴卡。",
  "requirements": [ { "text": "生成第一张图鉴卡", "done": false } ],
  "rewards": { "score": 20 } }
```

- `requirements[].done`：**整枚勋章同一状态**（解锁了全是 true，否则全 false），做不了单条勾选
- `rewards.score` 就是经验值，前端展示成「经验 +20」
- `title` 是墙上用的短名、`displayTitle` 是详情/分享用的完整名（见 §五.4）
- `404 勋章不存在`

### `POST /medals/{medal_id}/share`

```http
POST /api/v1/medals/medal_7/share
{ "channel": "wechatFriend" }
```

`channel` 可选，默认 `wechatFriend`（服务端目前不区分渠道，原样不校验）。

```json
{ "title": "我在追潮记点亮了「深蓝百万里」勋章",
  "imageUrl": "https://www.blueakaiwu.cn/api/v1/static/assets/badges/crab-map.png",
  "path": "/pages/achieve/achieve?medalId=medal_7",
  "copyText": "我在追潮记点亮了「深蓝百万里」勋章，一起来探索海岸吧！" }
```

- `imageUrl` 就是这枚勋章的 `iconUrl`；**老 5 枚勋章是空串**（没配图案），分享时注意兜底
- 只按 `medal_id` 生成文案，**不校验调用者是否真解锁了这枚**
- `404 勋章不存在`

### `GET /achievements/friends`

```json
{ "list": [ { "userId": "u_…", "nickname": "…", "avatarUrl": "/users/u_…/avatar",
              "level": 3, "score": 120, "me": true } ] }
```

首版**固定只返回自己**（没有社交关系）。`me` 恒为 `true`。

### `GET /achievements/leaderboard`

| query | 默认 | 说明 |
|---|---|---|
| `scope` | `all` | `all` = 全站榜；`friends` = 我关注的人 |
| `page` | `1` | ≥1 |
| `pageSize` | `20` | 1–100 |
| `limit` | 无 | 兼容旧调用：只取前 N 名（传了它 `page` 强制为 1） |

```json
{ "list": [ { "rank": 1, "userId": "u_…", "nickname": "…", "avatarUrl": "",
              "level": 2, "score": 140, "me": false, "followed": false } ],
  "page": 1, "pageSize": 3, "total": 12 }
```

- 排序：`score` 倒序
- `level` 读的是 `users.level`（同步时机见 §八），和 `score` 不一定严格对得上 ——
  还取决于这个人去过多少点位
- `scope=friends` 且未登录 → 空列表（不报错）；**好友榜里包含自己**，方便对比
- `followed` = 当前用户有没有关注这一行的人

### `POST /users/{user_id}/follow`

无 body。

```json
{ "followed": true, "followerCount": 3 }
```

| 情况 | 结果 |
|---|---|
| 成功 | `followed: true` |
| 重复关注 | 幂等，仍返回 `followed: true`，不报 409 |
| 关注自己 | `400 不能关注自己` |
| 用户不存在 | `404 用户不存在` |

### `DELETE /users/{user_id}/follow`

```json
{ "followed": false, "followerCount": 3 }
```

- 没关注过也返回成功（不报 404）；用户不存在 → `404 用户不存在`

### 哪些接口会返回 `unlockedMedalIds`

成就不是在成就页解锁的，而是**用户做完某个动作时顺手结算**，把新解锁的勋章 id 塞进那个
动作的响应里。所以下面这些接口都可能带 `unlockedMedalIds`，前端拿到非空就弹解锁动画：

| 接口 | 可能解锁的成就 |
|---|---|
| `POST /ai/species-guess` | 识别类：蟹蟹 / 蟹老板 / “海景房” / 海星拾趣 / 海洋记录员 / 海的味道 / 蛏子 / 藤壶 / 对照图鉴猜对 |
| `POST /ai/trash-guess` | 深蓝小卫士 |
| `POST /cards` | 初次见面、探索新星（3 个点位打卡）、收集达人（5 张图鉴卡） |
| `POST /watch/sessions/{session_id}/species` | siuuuuuu（集齐「螺」） |
| `POST /quizzes/{quiz_id}/questions`、`POST /quizzes/{quiz_id}/submit` | 无畏冒险家（安全观察闯关全对） |
| `POST /achievements/report` | 深蓝百万里 |
| `POST /community/notes`、`POST /explore/sessions`、`POST /light-maps/{map_id}/visits` | 目前没有对应的成就，接口仍带这个字段（恒空数组） |

这套约定的老实现见首页 `enqueueUnlocks` / `flushUnlocks` 与 `utils/unlock.ts`，
新接口照抄即可。

---

## 十、出行建议改为「每日预生成」

**为什么**：小程序不允许跟 AI 对话，所以建议不能「用户点一次、后端调一次 AI」。
改成：**每天凌晨 4 点（北京时间）对每个点位生成当天的建议，存库；接口只读存好的那份。**

### 生成与存储

- 后端**进程内后台任务**（随服务启动），每天 **04:00** 跑一次；服务启动时若已过 4 点、
  当天还没生成，**自动补跑一次**（兜底「4 点服务器没在跑」的情况）。幂等。
- 存表 `spot_advice`：`spot_id + date`（北京时间 `YYYY-MM-DD`）唯一，一条一份。
  `headline` / `body` 是 AI 写的文案；`generated_by` 记 `ai` 还是 `rule`
  （AI 失败时降级成规则模板，不空着）。
- 生成时用的「参考时刻」是**当天 12:00**，不是在跑的这一刻 —— 潮汐服务在 06:00 前一律
  判「现在不适合赶海」，凌晨 4 点直接生成会把一整天都写成「不适合」。
- 规模：38 个点位 × 1 次 qwen-plus，并发 4，约 1~2 分钟。

### 接口变化

`POST /ai/tide-advice` 与 `GET /home/today`（`withAdvice=1`）：

| 字段 | 现在是 | 说明 |
|---|---|---|
| `headline` / `body` | **读库**（当天 4 点生成的那份） | 库里没有（新点位 / 当天还没跑到）→ 退回规则模板 |
| `suitableNow` / `bestTimeFrom` / `bestTimeTo` / `leaveBefore` / `recommendedSpot` | **仍按请求时刻实时算** | 不冻结 —— 晚上打开不会显示白天才成立的内容 |
| `adviceDate` | **新增** | 这份文案算的是哪天（`YYYY-MM-DD`） |
| `generatedAt` | **新增** | 文案生成时间；退回规则模板时为 `null` |
| `generatedBy` | 值不变 | `ai` / `rule` |

**响应结构没变**（只有新增字段，没有改名/删除），前端不接也能照常跑。

> 顺带修了个老问题：`POST /ai/tide-advice` 原来把 `date` 透传给了潮汐服务，而潮汐服务在
> 传了日期时会把 `trend` / `currentHeightM` 取成**那天 12:00** 的值 —— 所以「现在适不适合
> 赶海」其实一直是按天算的。现在改成按请求时刻算，与 `/home/today` 口径一致。

### ⚠️ 前端待办：这张卡片现在不显示 AI 文案

`pages/home/home.ts` 的 `normalizeAdvice` 只取 `suitableNow` / `bestTimeFrom` / `bestTimeTo` /
`recommendedSpot` 四个字段，**`headline` / `body` 一个字都没渲染**（旧的长文解析
`filterAdviceBody` 已是死代码）。所以**后端这轮做完、前端不动的话，用户看不到任何变化**。

要让「预生成好的建议」真的露出来，前端需要渲染 `advice.headline`（当标题）与
`advice.body`（当正文）。接口这边已经备好，随时可接。

### 手动补生成（管理端）

```
POST /api/v1/admin/advice/generate?date=2026-10-06&force=0
Header: X-Admin-Token: <ADMIN_TOKEN>

→ { "date": "2026-10-06", "total": 38, "generated": 38, "skipped": 0, "failed": 0 }
```

- 不传 `date` → 默认今天（北京时间）；`force=1` 覆盖重生成，默认跳过已生成的
- 用途：补历史、某天生成失败后重跑、**新增点位后补当天**

---

## 十一、赶海点点亮（「去过」的点位）

「我的赶海点」页（`pages/spot-collection`）展示用户在全部赶海点里的点亮进度。
**前端早就把这两个接口的契约写好了**（`services/api.ts` 的 `spotVisitApi`），
但后端一直没实现 —— 页面一直 404、观潮结束时的点亮一直静默失败。这一节把它补上。

### `GET /spots/visited` — 我去过的点位

需登录。

```json
{ "total": 43, "visitedCount": 2,
  "list": [ { "id": "spot_qd_01", "name": "青岛·会场赶海园", "city": "青岛",
              "district": "崂山区", "icon": "", "visited": true } ] }
```

- `list` **覆盖全部点位**，逐个带 `visited` —— 前端据此画点亮墙（一排 4 个）与顶部进度条
- `total` = 全部点位数（当前 43 = 42 个策展点 + 1 个用户投稿审核通过的），
  `visitedCount` = 去过的数量
- `icon` 是点位封面图（对象存储地址，**可能是空串**，前端自己兜底默认图）

### `POST /spots/{spot_id}/visit` — 点亮一个点位

需登录，**幂等**。前端在**观潮结束时**调它（`utils/watchBall.ts`）。

```http
POST /api/v1/spots/spot_qd_01/visit
{ "sessionId": "wr_…", "visitedAt": "2026-10-06T18:40:00+08:00" }
```

body 两个字段都可选，服务端只作追溯记录，不参与判定。

```json
{ "visited": true, "newlyVisited": true, "spotId": "spot_qd_01",
  "visitedSpotCount": 1, "xp": 60, "level": 2 }
```

| 情况 | 结果 |
|---|---|
| 首次点亮 | `newlyVisited: true`；`xp` / `level` 是**加过这次经验后**的最新值 |
| 重复点亮 | `newlyVisited: false`，其余照常返回（不报错、不重复给经验） |
| 点位不存在 | `404 点位不存在` |
| 未登录 | `401 未登录` |

> 顺带返回 `xp` / `level` 是为了让前端点完就能弹「+60 经验」，不用再查一次 `/auth/me`。

### 「去过」的口径

三个来源**取并集**，任一有记录就算去过（`app/services/levels.py::visited_spot_ids`）：

1. **`spot_visits`** —— 本节的显式点亮（观潮结束时前端调）
2. **`watch_records`** —— 观潮记录
3. **`checkins`** —— 打卡记录

这样**老用户的观潮/打卡历史不用补点亮也算数**，也不会因为前端什么时候开始调点亮接口而漏算。

### 经验的两个渠道（与 §八 同一套）

```
xp = 成就值 + 去过的点位数 × 60
```

| 渠道 | 怎么来 |
|---|---|
| **成就值** | 已解锁勋章的 `rewards.score` 累计（18 枚全拿 = 1320） |
| **去新点位** | 每去一个（新）点位 **+60**（`XP_PER_SPOT`，≈ 一枚稀有勋章） |

`/auth/me` 与 `/achievements/overview` 都返回 `xp` / `xpToNext` / `levelProgress` /
`visitedSpotCount`，完整说明见 §八。

> ✅ 这两个接口**前端不用改** —— `spotVisitApi.visited()` / `.light()` 早就在调了，
> 后端补上就通了。

---

## 十二、海洋图鉴搜索

图鉴页（`pages/wiki`）要加搜索框，搜的是图鉴里的生物。**不新增接口** —— 搜索就是
`GET /encyclopedia` 多带一个 `keyword`，返回结构和分类列表**一模一样**，前端拿到直接渲染。

### `GET /encyclopedia?keyword=蟹`

| query | 说明 |
|---|---|
| `keyword` | 关键词。可省略；省略 = 原来的「按分类列全部」 |
| `category` | 分类过滤（`shell` / `crab` / `algae` / `fish` / `other`），可与 `keyword` 同时用 |
| `page` / `pageSize` | 分页，默认 1 / 20 |

**匹配范围**：`name`（名称）、`aka`（别名）、`summary`（简介）、`habitat`（栖息地）—— 任一命中即算。
（原来只匹配 `name` + `aka`，正文里提到的搜不到。）

**排序按命中位置**（这轮新加；不排的话搜「蟹」会先蹦出正文里提了一嘴蟹的物种）：

1. 名称**完全相同**
2. 名称**前缀**命中（`蟹…`）
3. 名称**包含**
4. **别名**命中
5. **简介 / 栖息地**命中

同档内按 `id` 稳定排序；**没传 `keyword` 时仍是原来的 `id` 排序**，老行为不变。

**响应**：与列表**完全一致**（`paginated` 信封）：

```json
{ "list": [ { "id": "sp_clam", "name": "蛤蜊", "category": "shell",
              "coverUrl": "/encyclopedia/sp_clam/cover",
              "summary": "躲在沙滩里的小贝，退潮后能看见沙面上冒小气泡。",
              "protected": false, "lit": false } ],
  "page": 1, "pageSize": 20, "total": 2 }
```

- `list` 为空 + `total: 0` 即「没搜到」，前端给空态即可
- `lit` 仍是调用方身份是否点亮（游客按 `client:{id}` 算）
- `coverUrl` 是相对路径（前端拼 BASE_URL），可能为空串 —— 与列表页同一套兜底

**线上数据实测**：

| `keyword` | `total` | 前几个 |
|---|---|---|
| `蟹` | 6 | 招潮蟹、沙蟹、寄居蟹、石蟹、梭子蟹、中华鲎（名字命中排前面） |
| `螺` | 7 | 花螺、宝贝螺、狗爪螺、玉螺、海螺… |
| `礁石` | 25 | 简介 / 栖息地里提到礁石的都算 |
| `海参` | 1 | 海参 |
| `zzz不存在` | 0 | — |

> 关键词里的 `%` `_` 按**普通字符**处理（不当 SQL 通配符），输入 `%` 搜的是真带百分号的内容。

### 前端怎么接

图鉴页已经在调 `contentApi.encyclopedia({ category })`，加搜索只是多传一个参数：

```ts
contentApi.encyclopedia({ category, keyword })
```

不用新接口、不用改返回结构的解析。

---

## 十三、赶海点位搜索

首页那个搜索入口（「搜索潮汐 / 点位」→ `pages/search`）搜的是**赶海点位**，这轮把它修好了。

### 之前的问题：按区搜一个字都搜不出来

搜索只匹配 `name` / `city` / `observe_hint` / `description`，**没有 `district`** ——
而点位名里也不含区名（叫「青岛·石老人」，不叫「崂山区石老人」）。结果：

| 搜 | 改前 | 改后 |
|---|---|---|
| 黄岛 | **0** | **14** |
| 崂山 | 1 | 14 |
| 即墨 | **0** | 6 |
| 市南 | **0** | 8 |

另外排序固定按热度，跟相关度无关 —— 搜「赶海」时名字里带「赶海」的和描述里提一句的混在一起。

### 现在的行为

**匹配字段**：`name` 名称 / `district` 所属区 / `city` 城市 / `observe_hint` 观察提示 / `description` 描述。

**排序按命中位置**（同档内热度高的靠前，再按 `id` 稳定）：

1. **名称**命中
2. **所属区**命中
3. **城市**命中
4. 只在**观察提示 / 描述**里提到

> 点位名都带「青岛·」前缀，所以**没做**「名称前缀命中」那一档 —— 永远命中不了，做了是死代码。

**两个入口口径一致**：`GET /spots?keyword=` 与 `GET /search`（首页搜索）共用
`app/services/spot_search.py` 的同一套匹配 + 排序，同一个词不会在两个地方搜出不同结果。

> 关键词里的 `%` `_` 按**普通字符**处理（`autoescape`），输入 `%` 不会变成「匹配全部」。
> **不传 `keyword` 时行为完全不变**（仍按热度排）。

### 接口变化

**不新增接口。**

- **`GET /spots?keyword=`** —— 返回结构不变，只是匹配更全、排序更合理
- **`GET /search?keyword=`** —— 点位部分同上；**每个点位新增 `district` 字段**：

```json
{ "spots": { "list": [ { "id": "spot_qd_14", "name": "青岛·鱼鸣嘴", "city": "青岛",
                         "district": "黄岛区",
                         "observeHint": "常见生物：螃蟹、海螺、八爪鱼、海星。" } ],
             "page": 1, "pageSize": 20, "total": 14 },
  "species": { "list": [ … ], "page": 1, "pageSize": 20, "total": 0 } }
```

> 加 `district` 是因为**按区搜**的时候，列表里得看得出结果为什么匹配上。
> `pages/search` 现在渲染的是 `{{item.city}} {{item.observeHint}}`（会显示成
> 「青岛 常见生物：…」），建议改成显示区（`item.district`）更清楚。

### 实测（线上数据）

| `keyword` | `total` | 前几个 |
|---|---|---|
| `黄岛` | 14 | 鱼鸣嘴、连三岛、银沙滩、红石崖… |
| `赶海` | 6 | **会场赶海园、王哥庄赶海园、会场赶海园**（名字命中）→ 栈桥（描述命中） |
| `石老人` | 1 | 青岛·石老人 |
| `停车场` | 8 | 描述里提过停车场的 |
| `zzz不存在` | 0 | — |
| `%` | 0 | 没被当通配符 ✅ |
