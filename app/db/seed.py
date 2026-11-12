"""首期种子数据：点位 / 图鉴 / 闯关 / 勋章。

幂等：仅当对应表为空时写入，可重复执行。
"""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Activity,
    CommunityTopic,
    Favorite,
    Knowledge,
    LightMap,
    Medal,
    Quiz,
    QuizQuestion,
    Species,
    SpeciesPhoto,
    Spot,
    SpotHarmonics,
    User,
    UserMedal,
)

# 已废弃的图鉴条目：早期用作「兜底泛称」，现在 AI 认不出会自己给答案，不再需要。
# 启动时会把这些条目连同相关的收藏/照片一起清掉。
DEPRECATED_SPECIES = ("sp_shore_crab", "sp_small_fish")

# 已下线点位：坐标无法确认，先从点位列表摘掉，等实地核对坐标再补回。
# 红石崖 / 红树林 是地理编码按「地名」搜出来的落点不对（分别落在街道中心、
# 城阳区的产业园）；韩家岭村落点在 20km 外的内陆同名村；顾家岛码头高德搜不到。
# 启动时连同它们的调和常数一起删（见 _remove_deprecated_spots）。
DEPRECATED_SPOTS: tuple[str, ...] = (
    "spot_qd_17",  # 青岛·红石崖
    "spot_qd_24",  # 青岛·顾家岛码头
    "spot_qd_25",  # 青岛·红树林
    "spot_qd_39",  # 青岛·韩家岭村
)

# 已废弃的勋章：最早那 5 枚（初次见面/探索新星/无畏冒险家/宝藏猎人/解谜达人）。
# 前端正式版只认众筹的那 13 枚（图片路径都写死了），后端配合收成 13 枚。
# 启动时把这 5 枚连同「已发给用户的记录」一起删掉，并把受影响用户的积分重算
# —— 勋章没了分还留着的话，完成度会超过 100%、等级也会虚高（见 _remove_deprecated_medals）。
DEPRECATED_MEDALS: tuple[str, ...] = (
    "medal_1",  # 初次见面
    "medal_2",  # 探索新星
    "medal_3",  # 无畏冒险家
    "medal_4",  # 宝藏猎人
    "medal_5",  # 解谜达人
)

# ---- 临时下线开关（测试用）----
# 想临时从图鉴里隐藏某个物种，就把它的 id 加进这个元组，重启后端即可。
# 恢复时**只需把 id 从这里删掉**（SPECIES 里的条目不用动），重启后会自动重新出现。
HIDDEN_SPECIES: tuple[str, ...] = (
    "sp_korean_rockfish",  # 许氏平鲉：暂时下线，方便测试其他内容
)

SPOTS = [
    {
        "id": "spot_qd_01",
        "name": "青岛·会场赶海园",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛤蜊、螃蟹、月亮贝、蛏子、海蛎子。",
        "description": "以沙滩为主，有小部分礁石区域，小白可冲，越往沙滩里面向着大海方向 走越容易出货。导航：蓝谷快线浦里站下，而后导航至会场赶海园1号门",
        "gear_list": [],
        "source": "追潮记团队整理（极热门·区内第1名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.293739,
        "lng": 120.65105,
    },
    {
        "id": "spot_qd_shilaoren",
        "name": "青岛·石老人",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海星、海螺、八带。",
        "description": "礁石。导航：导航石老人房车营地",
        "gear_list": [],
        "source": "追潮记团队整理（极热门·区内第2名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.09168,
        "lng": 120.46801,
    },
    {
        "id": "spot_qd_03",
        "name": "青岛·小麦岛",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海螺、螃蟹、海参、海胆。",
        "description": "礁石。导航：导航至小麦岛，环岛，桥两侧礁石都可以赶海",
        "gear_list": [],
        "source": "追潮记团队整理（热门·区内第3名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.054245,
        "lng": 120.431045,
    },
    {
        "id": "spot_qd_04",
        "name": "青岛·王哥庄赶海园",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛤蜊、蛏子。",
        "description": "沙滩。导航：导航至王哥庄赶海园或者海景馨苑",
        "gear_list": [],
        "source": "追潮记团队整理（热门·区内第4名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.272146,
        "lng": 120.654842,
    },
    {
        "id": "spot_qd_05",
        "name": "青岛·沙子口",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、蛤蜊、蛏子、猫眼螺、螃蟹。",
        "description": "沙滩。导航：导航至沙子口沙滩露营地/路创新科后湾地面停车场",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第5名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.113192,
        "lng": 120.543701,
    },
    {
        "id": "spot_qd_06",
        "name": "青岛·极地海洋公园",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海参、海螺、海胆。",
        "description": "礁石。导航：导航至 良友·海鲜味道/定海神针",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第6名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.069211,
        "lng": 120.442663,
    },
    {
        "id": "spot_qd_07",
        "name": "青岛·青岛雕塑园",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、沙板鱼、海螺、海星、海胆。",
        "description": "半礁石半沙滩。导航：导航至雕塑园停车场3号门",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第7名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.079683,
        "lng": 120.454678,
    },
    {
        "id": "spot_qd_08",
        "name": "青岛·海之风广场",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海参、海胆、八爪鱼、海星、鲍鱼。",
        "description": "为礁石滩地形。导航：导航至海之风广场停车场",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第8名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.073812,
        "lng": 120.44834,
    },
    {
        "id": "spot_qd_09",
        "name": "青岛·港东码头",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛤蜊、蛏子、螃蟹。",
        "description": "沙滩、礁石。导航：导航至港东海防工作站",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.27905,
        "lng": 120.675579,
    },
    {
        "id": "spot_qd_luqinghe",
        "name": "青岛·流清河沙滩",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海星、海胆、八带、墨鱼、面条鱼。",
        "description": "礁石。导航：导航流清湾沙滩或流清河沙滩停车场",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.12522,
        "lng": 120.614519,
    },
    {
        "id": "spot_qd_11",
        "name": "青岛·南姜码头",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海参、海螺、海胆、八爪鱼。",
        "description": "礁石。导航：导航至南姜码头海鲜市场停车场",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.099527,
        "lng": 120.536891,
    },
    {
        "id": "spot_qd_12",
        "name": "青岛·青岛铭都海岸酒店附近",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、蛤蜊。",
        "description": "礁石、沙滩。导航：导航至青岛铭都海岸酒店",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.110295,
        "lng": 120.539223,
    },
    {
        "id": "spot_qd_13",
        "name": "青岛·流清河社区附近",
        "city": "青岛",
        "district": "崂山区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、鱼、海星。",
        "description": "礁石。导航：导航至流清河社区公交站，走到马路海滩即可",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.127041,
        "lng": 120.621997,
    },
    {
        "id": "spot_qd_14",
        "name": "青岛·鱼鸣嘴",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、八爪鱼、海星。",
        "description": "礁石。导航：导航至鱼鸣嘴村",
        "gear_list": [],
        "source": "追潮记团队整理（极热门·区内第1名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.894416,
        "lng": 120.176716,
    },
    {
        "id": "spot_qd_15",
        "name": "青岛·连三岛",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海蛎子、螃蟹、海螺、海星、海胆、八爪鱼、海参、鲍鱼。",
        "description": "礁石。导航：导航至连三岛",
        "gear_list": [],
        "source": "追潮记团队整理（热门·区内第2名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.893377,
        "lng": 120.200093,
    },
    {
        "id": "spot_qd_16",
        "name": "青岛·银沙滩",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海参、海胆、海螺、八带、螃蟹。",
        "description": "礁石。导航：导航至银沙滩景区南110米/银沙滩3号门",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第3名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.919307,
        "lng": 120.203651,
    },
    {
        "id": "spot_qd_18",
        "name": "青岛·唐岛湾南岸滩涂",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、皮皮虾、蛤蜊、蛏子、海星。",
        "description": "沙滩。导航：导航至唐岛湾3号停车场/TAG西海美术馆/董家河村北193米",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第5名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.926987,
        "lng": 120.199437,
    },
    {
        "id": "spot_qd_19",
        "name": "青岛·灵山卫地铁站南侧滩涂",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛤蜊、蛏子、螃蟹。",
        "description": "泥滩。导航：导航至灵山卫地铁站，随后去往灵山湾北侧滩涂区域即可",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第6名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.92576,
        "lng": 120.138472,
    },
    {
        "id": "spot_qd_20",
        "name": "青岛·星光岛",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海参、八带、蛤蜊、螃蟹、海螺。",
        "description": "礁石。导航：导航至灵山湾和玺营销中心，过马路到对面桥底下或导航至星光岛2号桥",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第7名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.89421,
        "lng": 120.106807,
    },
    {
        "id": "spot_qd_21",
        "name": "青岛·石雀滩",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、海星。",
        "description": "礁石。导航：导航至石雀滩7号～8号停车场/SLOW HOUR",
        "gear_list": [],
        "source": "追潮记团队整理（一般·区内第8名）",
        "reviewed_at": "2026-10-04",
        "lat": 35.948217,
        "lng": 120.244579,
    },
    {
        "id": "spot_qd_22",
        "name": "青岛·南屯码头",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海螺、螃蟹、海星。",
        "description": "礁石。导航：导航至南屯码头",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 35.969802,
        "lng": 120.269815,
    },
    {
        "id": "spot_qd_23",
        "name": "青岛·虹桥待月公园",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛤蜊、蛏子、海星、海螺、蝼蛄虾。",
        "description": "半沙滩半礁石。导航：导航至虹桥待月公园",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 35.921758,
        "lng": 120.138033,
    },
    {
        "id": "spot_qd_26",
        "name": "青岛·海军公园",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、蛤蜊、虾、海蛎子。",
        "description": "沙滩、礁石。导航：导航至海军公园栈桥",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 35.819673,
        "lng": 120.031438,
    },
    {
        "id": "spot_qd_27",
        "name": "青岛·贝壳广场",
        "city": "青岛",
        "district": "黄岛区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛤蜊、螃蟹。",
        "description": "沙滩。导航：导航贝壳广场或城市阳台4号停车场",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 35.859282,
        "lng": 120.052872,
    },
    {
        "id": "spot_qd_28",
        "name": "青岛·栈桥",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、螃蟹、蛏子、鱼、蛤蜊、海螺、海胆、鹅蛋蚬、海肠。",
        "description": "半礁石半沙滩。导航：导航至栈桥，栈桥的西边、东边都可以赶海",
        "gear_list": [],
        "source": "追潮记团队整理（极热门·区内第1名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.061736,
        "lng": 120.3193,
    },
    {
        "id": "spot_qd_29",
        "name": "青岛·太平角公园",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、螃蟹、鱼、海胆、海参。",
        "description": "半礁石半沙滩。导航：导航至金海角度假园/ 海遇园",
        "gear_list": [],
        "source": "追潮记团队整理（极热门·区内第2名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.053038,
        "lng": 120.360265,
    },
    {
        "id": "spot_qd_yigong",
        "name": "青岛·第一海水浴场",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、螃蟹、海胆、多宝、沙板鱼、猫眼螺、海参。",
        "description": "半沙滩半礁石。导航：导航海瓜子便利店",
        "gear_list": [],
        "source": "追潮记团队整理（热门·区内第3名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.057428,
        "lng": 120.341325,
    },
    {
        "id": "spot_qd_31",
        "name": "青岛·台湾路",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海星、海参、八爪鱼。",
        "description": "礁石。导航：导航至融悦庭酒店",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第4名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.062461,
        "lng": 120.409946,
    },
    {
        "id": "spot_qd_32",
        "name": "青岛·东海饭店",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、八爪鱼、海胆。",
        "description": "礁石，附近有废弃鲍鱼池。导航：导航至东海饭店",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第5名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.048855,
        "lng": 120.344593,
    },
    {
        "id": "spot_qd_33",
        "name": "青岛·燕儿岛假日海滩",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、螃蟹、鱼、海参、海胆。",
        "description": "礁石。导航：导航至假日海滩",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.055542,
        "lng": 120.40785,
    },
    {
        "id": "spot_qd_34",
        "name": "青岛·奥帆中心",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、螃蟹、鱼、海参、海胆。",
        "description": "礁石。导航：导航至奥林匹克帆船中心-4号门",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.054643,
        "lng": 120.395063,
    },
    {
        "id": "spot_qd_35",
        "name": "青岛·银海大世界",
        "city": "青岛",
        "district": "市南区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：海星、螃蟹、鱼、海参、海胆。",
        "description": "礁石。导航：导航至银海大世界-西北门",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.057818,
        "lng": 120.422801,
    },
    {
        "id": "spot_qd_36",
        "name": "青岛·滨海公园",
        "city": "青岛",
        "district": "即墨区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：蛏子、蛤蜊、海螺、螃蟹。",
        "description": "沙滩。导航：导航至山东大学青岛校区东门，马路对面即可",
        "gear_list": [],
        "source": "追潮记团队整理（热门·区内第1名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.374702,
        "lng": 120.691338,
    },
    {
        "id": "spot_qd_37",
        "name": "青岛·盘龙庄村附近",
        "city": "青岛",
        "district": "即墨区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、蛤蜊。",
        "description": "礁石。导航：导航至盘龙庄村",
        "gear_list": [],
        "source": "追潮记团队整理（较热门·区内第2名）",
        "reviewed_at": "2026-10-04",
        "lat": 36.337454,
        "lng": 120.685221,
    },
    {
        "id": "spot_qd_38",
        "name": "青岛·冯家河村",
        "city": "青岛",
        "district": "即墨区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、蛤蜊。",
        "description": "礁石。导航：导航至冯家河村",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.33622,
        "lng": 120.70048,
    },
    {
        "id": "spot_qd_40",
        "name": "青岛·于家沟",
        "city": "青岛",
        "district": "即墨区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、蛤蜊。",
        "description": "礁石。导航：导航至余家沟",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.33736,
        "lng": 120.713661,
    },
    {
        "id": "spot_qd_41",
        "name": "青岛·羊羔涧",
        "city": "青岛",
        "district": "即墨区",
        "cover_key": "",
        "open_time": "",
        "age_hint": "",
        "safety_tags": [],
        "observe_hint": "常见生物：螃蟹、海螺、蛤蜊。",
        "description": "礁石。导航：导航至羊羔涧",
        "gear_list": [],
        "source": "追潮记团队整理（其他（平列）·未入榜（区内平列））",
        "reviewed_at": "2026-10-04",
        "lat": 36.334812,
        "lng": 120.721537,
    },
    {
        "id": "spot_wh_chengshantou",
        "name": "威海·成山头",
        "city": "威海",
        "district": "荣成市",
        "cover_key": "",
        "open_time": "全年开放",
        "age_hint": "适合 8 岁以上，风大需防风",
        "safety_tags": ["注意风浪", "远离崖边"],
        "observe_hint": "崖下礁石区在退潮时露出，能看到海葵与海星。",
        "description": "中国大陆海岸线东端，礁石陡峭，是观察岩岸生物的特色点位。",
        "gear_list": ["防滑鞋", "防风外套"],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "lat": 37.401724,
        "lng": 122.698982,
    },
    {
        "id": "spot_xm_01",
        "name": "厦门·白城沙滩",
        "city": "厦门",
        "district": "思明区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、寄居蟹、贝类。",
        "description": "沙滩。紧邻厦门大学，退潮后滩面较宽。导航：厦大白城沙滩。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.432281,
        "lng": 118.100875
    },
    {
        "id": "spot_xm_02",
        "name": "厦门·黄厝海滩",
        "city": "厦门",
        "district": "思明区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、寄居蟹、贝类。",
        "description": "沙滩。环岛路沿线，沙质细，退潮后可在滩面观察。导航：黄厝海滩。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.442614,
        "lng": 118.1619
    },
    {
        "id": "spot_xm_03",
        "name": "厦门·观音山沙滩",
        "city": "厦门",
        "district": "思明区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、小海螺、贝类。",
        "description": "沙滩。环岛东路观音山段，滩面开阔。导航：观音山沙滩。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.493697,
        "lng": 118.199506
    },
    {
        "id": "spot_xm_04",
        "name": "厦门·五缘湾湿地公园",
        "city": "厦门",
        "district": "湖里区",
        "cover_key": "",
        "open_time": "以公园开放时间为准",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "滩涂陷脚",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：招潮蟹、弹涂鱼、贝类。",
        "description": "内湾湿地与滩涂。退潮时可见滩涂生物，注意不要进入保护区围栏。导航：五缘湾湿地公园。",
        "gear_list": [
            "防滑雨靴"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.516071,
        "lng": 118.178133
    },
    {
        "id": "spot_xm_05",
        "name": "厦门·杏林湾",
        "city": "厦门",
        "district": "集美区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "滩涂陷脚",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：招潮蟹、弹涂鱼、贝类。",
        "description": "内湾滩涂。退潮后滩面露出，适合观察招潮蟹和弹涂鱼。导航：杏林湾绿道公园。",
        "gear_list": [
            "防滑雨靴"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.589993,
        "lng": 118.067284
    },
    {
        "id": "spot_xm_06",
        "name": "厦门·大嶝岛",
        "city": "厦门",
        "district": "翔安区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：蛤蜊、沙蟹、海蛎子。",
        "description": "海岛沙滩与滩涂。退潮后滩面较宽，注意避开养殖区。导航：大嶝岛。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.548894,
        "lng": 118.342906
    },
    {
        "id": "spot_xm_07",
        "name": "厦门·小嶝岛",
        "city": "厦门",
        "district": "翔安区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合 8 岁以上，需家长陪同",
        "safety_tags": [
            "礁石湿滑",
            "注意涨潮",
            "远离浪大区域"
        ],
        "observe_hint": "常见生物：海蛎子、藤壶、小螃蟹。",
        "description": "海岛礁石与沙滩。礁石区湿滑，注意涨潮。导航：小嶝岛。",
        "gear_list": [
            "防滑鞋",
            "手套"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.555919,
        "lng": 118.38697
    },
    {
        "id": "spot_xm_08",
        "name": "厦门·海沧湾公园",
        "city": "厦门",
        "district": "海沧区",
        "cover_key": "",
        "open_time": "以公园开放时间为准",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "滩涂陷脚",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：招潮蟹、弹涂鱼、贝类。",
        "description": "海湾滩涂。退潮后可在滩面观察，注意淤泥。导航：海沧湾公园。",
        "gear_list": [
            "防滑雨靴"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.48805,
        "lng": 118.054462
    },
    {
        "id": "spot_xm_09",
        "name": "厦门·鼓浪屿港仔后沙滩",
        "city": "厦门",
        "district": "思明区",
        "cover_key": "",
        "open_time": "以轮渡和景区开放时间为准",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：寄居蟹、小螃蟹、贝类。",
        "description": "沙滩。鼓浪屿港仔后海滨，游客较多，退潮后可在沙滩观察。导航：港仔后海滨沙滩。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 24.439277,
        "lng": 118.066849
    },
    {
        "id": "spot_hn_hk_01",
        "name": "海口·假日海滩",
        "city": "海口",
        "district": "秀英区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、寄居蟹、贝类。",
        "description": "沙滩。海口西海岸，滩面平缓。导航：假日海滩旅游区。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 20.036599,
        "lng": 110.237188
    },
    {
        "id": "spot_hn_hk_02",
        "name": "海口·西秀海滩",
        "city": "海口",
        "district": "秀英区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、贝类。",
        "description": "沙滩。海口西海岸，适合散步和观察。导航：西秀海滩公园。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 20.02537,
        "lng": 110.260292
    },
    {
        "id": "spot_hn_hk_03",
        "name": "海口·白沙门沙滩",
        "city": "海口",
        "district": "美兰区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、寄居蟹、贝类。",
        "description": "沙滩。海口海甸岛北侧，退潮后滩面较宽。导航：白沙门沙滩。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 20.074992,
        "lng": 110.333659
    },
    {
        "id": "spot_hn_hk_04",
        "name": "海口·东寨港红树林",
        "city": "海口",
        "district": "美兰区",
        "cover_key": "",
        "open_time": "以景区开放时间为准",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "保护区内勿进入",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：招潮蟹、弹涂鱼、螺类（保护区内请勿进入）。",
        "description": "红树林滩涂。保护区核心区禁止进入，仅在外围开放区域观察。导航：东寨港红树林旅游区。",
        "gear_list": [
            "防滑鞋",
            "望远镜"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.950519,
        "lng": 110.582606
    },
    {
        "id": "spot_hn_wc_01",
        "name": "文昌·清澜港",
        "city": "文昌",
        "district": "文城镇",
        "cover_key": "",
        "open_time": "以港区开放情况为准",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意船只",
            "滩涂陷脚",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：螃蟹、贝类。",
        "description": "渔港与滩涂。退潮时港区滩涂露出，注意船只和湿滑。导航：清澜港。",
        "gear_list": [
            "防滑雨靴",
            "手套"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.550841,
        "lng": 110.833449
    },
    {
        "id": "spot_hn_wc_02",
        "name": "文昌·铜鼓岭石头公园",
        "city": "文昌",
        "district": "龙楼镇",
        "cover_key": "",
        "open_time": "以景区开放时间为准",
        "age_hint": "适合 8 岁以上，需家长陪同",
        "safety_tags": [
            "礁石湿滑",
            "注意涨潮",
            "远离浪大区域"
        ],
        "observe_hint": "常见生物：海螺、藤壶、小螃蟹。",
        "description": "礁石海岸。火山岩礁石湿滑且浪大，注意安全。导航：石头公园。",
        "gear_list": [
            "防滑鞋",
            "手套"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.639504,
        "lng": 111.035901
    },
    {
        "id": "spot_hn_qh_01",
        "name": "琼海·博鳌玉带滩",
        "city": "琼海",
        "district": "博鳌镇",
        "cover_key": "",
        "open_time": "以船班时间为准",
        "age_hint": "适合 6 岁以上，需家长陪同并穿救生衣",
        "safety_tags": [
            "需乘船",
            "注意船班",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：沙蟹、贝类。",
        "description": "沙滩。万泉河入海口沙洲，需乘船前往，注意船班和涨潮。导航：博鳌玉带滩。",
        "gear_list": [
            "防滑鞋",
            "救生衣"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.106979,
        "lng": 110.577489
    },
    {
        "id": "spot_hn_wn_01",
        "name": "万宁·日月湾",
        "city": "万宁",
        "district": "日月湾",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意浪大",
            "远离冲浪区",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：沙蟹、寄居蟹、贝类。",
        "description": "沙滩。冲浪胜地，浪较大，赶海请远离冲浪区和深水区。导航：日月湾。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 18.628143,
        "lng": 110.216248
    },
    {
        "id": "spot_hn_wn_02",
        "name": "万宁·石梅湾",
        "city": "万宁",
        "district": "石梅湾",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、贝类。",
        "description": "沙滩。海湾沙滩，退潮后可在滩面观察。导航：石梅湾。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 18.661922,
        "lng": 110.27209
    },
    {
        "id": "spot_hn_ls_01",
        "name": "陵水·清水湾",
        "city": "陵水",
        "district": "英州镇",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、贝类。",
        "description": "沙滩。陵水清水湾，滩面平缓。导航：清水湾旅游区。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 18.394904,
        "lng": 109.869522
    },
    {
        "id": "spot_hn_sy_01",
        "name": "三亚·三亚湾",
        "city": "三亚",
        "district": "天涯区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、寄居蟹、贝类。",
        "description": "沙滩。三亚湾椰梦长廊沿线，退潮后滩面较宽。导航：三亚湾。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 18.282593,
        "lng": 109.464218
    },
    {
        "id": "spot_hn_sy_02",
        "name": "三亚·大东海",
        "city": "三亚",
        "district": "吉阳区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：小螃蟹、寄居蟹、贝类。",
        "description": "沙滩与礁石。游客较多，注意区分游泳区和礁石区。导航：大东海旅游区。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 18.22082,
        "lng": 109.522212
    },
    {
        "id": "spot_hn_sy_03",
        "name": "三亚·亚龙湾",
        "city": "三亚",
        "district": "吉阳区",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、贝类。",
        "description": "沙滩。海湾沙滩，沙质细。导航：亚龙湾海滩。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 18.229188,
        "lng": 109.646004
    },
    {
        "id": "spot_hn_dz_01",
        "name": "儋州·白马井",
        "city": "儋州",
        "district": "白马井镇",
        "cover_key": "",
        "open_time": "以港区开放情况为准",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意船只",
            "滩涂陷脚",
            "注意涨潮"
        ],
        "observe_hint": "常见生物：螃蟹、贝类。",
        "description": "渔港与滩涂。退潮时港区滩涂露出，注意船只。导航：白马井中心渔港码头。",
        "gear_list": [
            "防滑雨靴",
            "手套"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.714222,
        "lng": 109.225146
    },
    {
        "id": "spot_hn_df_01",
        "name": "东方·鱼鳞洲",
        "city": "东方",
        "district": "八所镇",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合 8 岁以上，需家长陪同",
        "safety_tags": [
            "礁石湿滑",
            "注意涨潮",
            "远离浪大区域"
        ],
        "observe_hint": "常见生物：海螺、藤壶、小螃蟹。",
        "description": "礁石海岸。岩石湿滑，浪大时不要下到礁石区。导航：鱼鳞洲自然风景保护区。",
        "gear_list": [
            "防滑鞋",
            "手套"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.097793,
        "lng": 108.617239
    },
    {
        "id": "spot_hn_cj_01",
        "name": "昌江·棋子湾",
        "city": "昌江",
        "district": "昌化镇",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "礁石湿滑",
            "注意涨潮",
            "远离浪大区域"
        ],
        "observe_hint": "常见生物：海螺、螃蟹、贝类。",
        "description": "礁石与沙滩。礁石区湿滑，注意涨潮。导航：棋子湾。",
        "gear_list": [
            "防滑鞋",
            "手套"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.372903,
        "lng": 108.699422
    },
    {
        "id": "spot_hn_cm_01",
        "name": "澄迈·盈滨半岛",
        "city": "澄迈",
        "district": "老城镇",
        "cover_key": "",
        "open_time": "全天开放",
        "age_hint": "适合全年龄，未成年人需家长陪同",
        "safety_tags": [
            "注意涨潮",
            "远离深水区"
        ],
        "observe_hint": "常见生物：沙蟹、贝类。",
        "description": "沙滩。澄迈盈滨半岛，滩面平缓。导航：盈滨半岛旅游区。",
        "gear_list": [
            "防滑鞋",
            "防晒用品"
        ],
        "source": "追潮记团队整理",
        "reviewed_at": "2026-10-30",
        "lat": 19.980712,
        "lng": 110.110492
    },
    {
        "id": 'spot_hn_ls_02',
        "name": '陵水·黎安哪吒亲子户外俱乐部',
        "city": '陵水',
        "district": '黎安镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合全年龄，未成年人需家长陪同',
        "safety_tags": ['注意涨潮', '远离深水区', '穿防滑鞋'],
        "observe_hint": '常见生物：寄居蟹、螃蟹、弹涂鱼、海星、海胆、蛤蜊。',
        "description": '泥沙混合滩。导航至陵水黎安哪吒亲子户外俱乐部。',
        "gear_list": ['防滑鞋', '小水桶', '观察盒'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.433956,
        "lng": 110.046908,
    },
    {
        "id": 'spot_hn_ls_03',
        "name": '陵水·海龟沙滩',
        "city": '陵水',
        "district": '黎安镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合全年龄，未成年人需家长陪同',
        "safety_tags": ['注意涨潮', '远离深水区', '穿防滑鞋'],
        "observe_hint": '常见生物：寄居蟹、螃蟹、弹涂鱼、海星、海胆、蛤蜊。',
        "description": '泥沙混合滩。导航至海龟沙滩，向南部步行可达，附近为海南生态海岸。',
        "gear_list": ['防滑鞋', '小水桶', '观察盒'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.443859,
        "lng": 110.053564,
    },
    {
        "id": 'spot_hn_ls_04',
        "name": '陵水·岭仔村',
        "city": '陵水',
        "district": '黎安镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合全年龄，未成年人需家长陪同',
        "safety_tags": ['注意涨潮', '远离深水区'],
        "observe_hint": '常见生物：贝壳、海星、小鱼、螃蟹。',
        "description": '沙滩。导航至岭仔村赶海点或岭仔村赶海小火锅。',
        "gear_list": ['防滑鞋', '防晒用品'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.419881,
        "lng": 110.04835,
    },
    {
        "id": 'spot_hn_ls_05',
        "name": '陵水·赤岭',
        "city": '陵水',
        "district": '英州镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合 6 岁以上，礁石湿滑需大人牵手',
        "safety_tags": ['礁石湿滑', '远离涨潮线', '穿防滑鞋'],
        "observe_hint": '常见生物：海胆、小螃蟹、蛤蜊、海螺、海星。',
        "description": '礁石滩。导航至中国石化赤岭加油站（英州镇赤岭村望海路2号）。',
        "gear_list": ['防滑鞋', '观察盒'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.383698,
        "lng": 109.82251,
    },
    {
        "id": 'spot_hn_ls_06',
        "name": '陵水·桐海村',
        "city": '陵水',
        "district": '新村镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合全年龄，未成年人需家长陪同',
        "safety_tags": ['注意涨潮', '远离深水区', '穿防滑鞋'],
        "observe_hint": '常见生物：螃蟹、海星、花甲、蛤蜊、青口、生蚝、海螺。',
        "description": '泥沙混合滩。导航至桐海小学（新村镇桐海村）。',
        "gear_list": ['防滑鞋', '小水桶', '观察盒'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.433498,
        "lng": 110.008927,
    },
    {
        "id": 'spot_hn_ls_07',
        "name": '陵水·新村港东侧滩涂',
        "city": '陵水',
        "district": '新村镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合全年龄，未成年人需家长陪同',
        "safety_tags": ['注意涨潮', '远离深水区', '穿防滑鞋'],
        "observe_hint": '常见生物：海星、海胆、螃蟹。',
        "description": '泥沙混合滩。位于海南陵水黎安国际教育创新试验区新视听艺术博物馆西北侧沙滩区域。',
        "gear_list": ['防滑鞋', '小水桶', '观察盒'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.401563,
        "lng": 110.01821,
    },
    {
        "id": 'spot_hn_ls_08',
        "name": '陵水·黎安港西北角',
        "city": '陵水',
        "district": '光坡镇',
        "cover_key": '',
        "open_time": '全天开放（退潮时段最佳）',
        "age_hint": '适合全年龄，未成年人需家长陪同',
        "safety_tags": ['注意涨潮', '远离深水区', '穿防滑鞋'],
        "observe_hint": '常见生物：招潮蟹、青蟹、蛏子、蛤蜊。',
        "description": '泥沙混合滩。导航至富力湾堤坝，后步行至堤坝东南方向滩涂区域。',
        "gear_list": ['防滑鞋', '小水桶', '观察盒'],
        "source": '海南陵水赶海点位表',
        "reviewed_at": '2026-10-30',
        "lat": 18.516925,
        "lng": 110.099565,
    },
]


SPECIES = [
    {
        "id": "sp_barnacle",
        "name": "藤壶",
        "aka": ["海蛀子", "马牙"],
        "category": "shell",
        "cover_key": "",
        "summary": "牢牢长在礁石上的小贝壳，退潮后会露出，是礁石上的常住居民。",
        "look": "身体被一圈圈灰白色硬壳围住，中央有个小口，会探出羽毛状的小脚。",
        "habitat": "潮间带礁石、码头、船底",
        "observe_tip": "退潮后用放大镜看壳口，耐心等一会儿能看到它伸小脚滤食。",
        "safety_tip": "壳边缘锋利，别用手抠，容易划伤。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_oyster",
        "name": "牡蛎",
        "aka": ["生蚝", "海蛎子"],
        "category": "shell",
        "cover_key": "",
        "summary": "紧贴礁石生长的贝类，壳粗糙坚硬，退潮时最容易看见。",
        "look": "外壳形状不规则，灰褐色，内部光滑有光泽。",
        "habitat": "潮间带礁石、岩石海岸",
        "observe_tip": "注意牡蛎壳上常常附着小藤壶，可以一并观察。",
        "safety_tip": "壳极锋利，近距离观察时别坐靠上去。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_limpet",
        "name": "帽贝",
        "aka": ["笠贝", "小斗笠"],
        "category": "shell",
        "cover_key": "",
        "summary": "像一顶小斗笠扣在礁石上，会慢慢移动啃食海藻。",
        "look": "贝壳呈斗笠状，表面有放射纹，颜色随环境变化。",
        "habitat": "潮间带岩石表面",
        "observe_tip": "记住它的位置，过一会儿回来看看它是不是挪了地方。",
        "safety_tip": "不要去抠它，离开水它会受伤。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_sea_lettuce",
        "name": "石莼",
        "aka": ["海白菜", "绿海藻"],
        "category": "algae",
        "cover_key": "",
        "summary": "薄薄的绿色海藻，退潮后像铺在礁石上的绿纱。",
        "look": "叶片薄而透明，鲜绿色，成片生长。",
        "habitat": "潮间带岩石、潮池",
        "observe_tip": "轻轻碰一下，感受它又滑又软，是很重要的海洋初级生产者。",
        "safety_tip": "表面湿滑，观察时踩稳了再动。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_hermit_crab",
        "name": "寄居蟹",
        "aka": ["海居蟹", "借壳蟹"],
        "category": "crab",
        "cover_key": "",
        "summary": "背着小房子到处跑的螃蟹，会“借”空螺壳当自己的家。",
        "look": "前半身是螃蟹，尾部卷曲藏在捡来的螺壳里，走路时会伸出两只小钳子。",
        "habitat": "潮间带滩涂、礁石缝隙",
        "observe_tip": "安静蹲下看它怎么搬家、怎么换壳，特别有趣。",
        "safety_tip": "不要把它从壳里拽出来，会伤害它。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_sea_anemone",
        "name": "海葵",
        "aka": ["海菊花"],
        "category": "other",
        "cover_key": "",
        "summary": "长得像花却不是花，会伸出触手捕食，潮池里常能见到。",
        "look": "身体圆筒状，顶端一圈软软的触手，像盛开的小花。",
        "habitat": "潮池、岩石缝隙",
        "observe_tip": "退潮后海葵会缩成小团，等水没过又会慢慢展开。",
        "safety_tip": "触手有粘性，别用手戳。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_starfish",
        "name": "海星",
        "aka": ["星鱼"],
        "category": "other",
        "cover_key": "",
        "summary": "长着五条腕的海洋动物，能慢慢爬行，还会把胃吐出来吃东西。",
        "look": "身体扁平，通常有五条腕，颜色有橙、红、紫等。",
        "habitat": "潮间带礁石、滩涂",
        "observe_tip": "看看它腹面的小管足，那才是它“走路”的工具。",
        "safety_tip": "别把它捞出水面太久，会脱水。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-01",
        "protected": False,
    },
    {
        "id": "sp_korean_rockfish",
        "name": "许氏平鲉",
        "aka": ["黑头鱼", "黑鲪", "黑石鲈"],
        "category": "fish",
        "cover_key": "",
        "summary": "近岸礁石区常见的小鱼，幼鱼常出现在潮池和浅水里，长大后体色变深。",
        "look": "身体侧扁、头较大；幼鱼体侧青绿带金属光泽，布满细密斑点，眼睛大而亮。",
        "habitat": "近岸礁石区、潮池、海藻丛",
        "observe_tip": "幼鱼常停在礁石边或海藻间不动，从侧面安静观察就能看很久。",
        "safety_tip": "背鳍有硬棘会扎手，只用眼睛看，不要徒手抓。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-09-11",
        "protected": False,
    },
    # ===== 贝类 / 螺类 =====
    {
        "id": "sp_mussel",
        "name": "贻贝",
        "aka": ["海虹", "淡菜"],
        "category": "shell",
        "cover_key": "",
        "summary": "一串串长在礁石缝里的小黑壳，退潮后常成群出现。",
        "look": "壳黑褐色、尖长，常一簇簇用足丝牢牢系在礁石上。",
        "habitat": "潮间带礁石、码头桩基",
        "observe_tip": "看看它们靠什么把自己“拴”在石头上，别硬拽下来。",
        "safety_tip": "壳边锋利，观察时小心别划手。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_clam",
        "name": "蛤蜊",
        "aka": ["花蛤", "沙蛤"],
        "category": "shell",
        "cover_key": "",
        "summary": "躲在沙滩里的小贝，退潮后能看见沙面上冒小气泡。",
        "look": "壳上有花纹，两片壳紧紧合在一起，埋在沙里只露一点点。",
        "habitat": "潮间带沙滩、泥沙地",
        "observe_tip": "看沙滩上冒小泡的小洞，下面可能就藏着它。",
        "safety_tip": "挖蛤蜊要在安全区域，注意别被碎壳划伤。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_scallop",
        "name": "扇贝",
        "aka": ["海扇"],
        "category": "shell",
        "cover_key": "",
        "summary": "壳像一把小扇子，还会“啪”地一下喷水游走。",
        "look": "两片壳像打开的折扇，有放射状的棱纹。",
        "habitat": "浅海、潮下带沙滩",
        "observe_tip": "退潮后常能捡到空壳，对着光看有彩色光泽。",
        "safety_tip": "活的扇贝壳会突然闭合，别把手伸进去。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_razor_clam",
        "name": "蛏子",
        "aka": ["竹蛏", "缢蛏"],
        "category": "shell",
        "cover_key": "",
        "summary": "长得像根细长的小竹管，会快速缩回沙洞里。",
        "look": "壳又长又薄，像一节小竹筒，表面光滑。",
        "habitat": "潮间带沙滩、泥沙滩",
        "observe_tip": "轻轻在洞口撒点盐，能看到它探出头又缩回去。",
        "safety_tip": "挖蛏子动作要轻，别破坏滩涂。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_abalone",
        "name": "鲍鱼",
        "aka": ["九孔螺", "石决明"],
        "category": "shell",
        "cover_key": "",
        "summary": "像半只小碗扣在礁石上，是名贵的海味。",
        "look": "壳扁平、一侧有一排小孔，紧紧吸附在礁石上。",
        "habitat": "潮下带礁石、岩缝",
        "observe_tip": "它吸力很大，观察就好，别尝试抠下来。",
        "safety_tip": "野生鲍鱼多受保护，不要采集。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_whelk",
        "name": "海螺",
        "aka": ["香螺", "脉红螺"],
        "category": "shell",
        "cover_key": "",
        "summary": "背着螺旋小房子，退潮后常藏在礁石下。",
        "look": "壳螺旋状、厚实，有的带花纹，壳口像小门。",
        "habitat": "潮间带礁石、泥沙底",
        "observe_tip": "翻看礁石下的海螺时，记得看完放回原处。",
        "safety_tip": "有些海螺可能有毒，不要用手直接摸壳内软体。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_cowry",
        "name": "宝贝螺",
        "aka": ["宝贝", "子安贝"],
        "category": "shell",
        "cover_key": "",
        "summary": "壳光滑得像瓷器，古时候还当过钱用。",
        "look": "壳光滑油亮、有斑点或条纹，腹面有一条细细的口。",
        "habitat": "潮间带礁石、珊瑚礁区",
        "observe_tip": "捡到空壳可以放在耳边听，有海浪声。",
        "safety_tip": "活的宝贝螺别捡，它需要回海水里。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_chiton",
        "name": "石鳖",
        "aka": ["海和尚", "铁甲鱼"],
        "category": "shell",
        "cover_key": "",
        "summary": "背上八块小甲板，像个小铁甲虫趴在礁石上。",
        "look": "身体扁椭圆，背上有八块壳板，边缘一圈软裙边。",
        "habitat": "潮间带礁石表面",
        "observe_tip": "它吸得很牢，轻轻碰它会缩成一团。",
        "safety_tip": "别硬抠，会伤到它。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_moon_snail",
        "name": "玉螺",
        "aka": ["月亮螺"],
        "category": "shell",
        "cover_key": "",
        "summary": "壳圆鼓鼓像个月亮，会在沙里慢慢爬。",
        "look": "壳近球形、光滑，有的大有的小，颜色淡。",
        "habitat": "潮间带沙滩、泥沙底",
        "observe_tip": "沙滩上那种圆圆的沙盘是它产的卵，可以看看别破坏。",
        "safety_tip": "观察后把它放回湿润的沙里。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    # ===== 蟹类 =====
    {
        "id": "sp_fiddler_crab",
        "name": "招潮蟹",
        "aka": ["海蟛蜞", "红螯蟹"],
        "category": "crab",
        "cover_key": "",
        "summary": "雄蟹有一只特别大的钳子，挥起来像在“招潮”。",
        "look": "雄蟹一只螯特别大，颜色鲜艳，另一只小小的。",
        "habitat": "泥滩、河口滩涂",
        "observe_tip": "站在远处看雄蟹挥大螯求偶，别追太近。",
        "safety_tip": "大螯会夹人，只看不抓。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_ghost_crab",
        "name": "沙蟹",
        "aka": ["鬼蟹", "幽灵蟹"],
        "category": "crab",
        "cover_key": "",
        "summary": "沙滩上跑得飞快的小蟹，一眨眼就钻进洞里。",
        "look": "颜色和沙子很像，眼睛竖起来像两根小柱。",
        "habitat": "高潮带沙滩",
        "observe_tip": "傍晚它们最活跃，蹲下静看它们挖洞。",
        "safety_tip": "别追着踩，可能踩塌它们的洞。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_rock_crab",
        "name": "石蟹",
        "aka": ["岩蟹", "石岩蟹"],
        "category": "crab",
        "cover_key": "",
        "summary": "躲在礁石缝里，壳硬邦邦像块小石头。",
        "look": "壳厚、颜色灰褐，钳子粗壮有力。",
        "habitat": "潮间带礁石缝隙",
        "observe_tip": "翻石头找它时，看完要把石头轻轻放回原位。",
        "safety_tip": "钳子有力，会夹手，别徒手抓。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_swimming_crab",
        "name": "梭子蟹",
        "aka": ["三疣梭子蟹", "海蟹"],
        "category": "crab",
        "cover_key": "",
        "summary": "壳两头尖像梭子，最后一对脚是“船桨”。",
        "look": "壳呈梭形，两端尖，最后两条腿扁扁的像桨。",
        "habitat": "浅海、潮下带泥沙底",
        "observe_tip": "退潮时偶尔能在浅水边看到它游动。",
        "safety_tip": "它游得快也会夹人，观察保持距离。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    # ===== 虾 / 节肢动物 =====
    {
        "id": "sp_mantis_shrimp",
        "name": "皮皮虾",
        "aka": ["虾蛄", "螳螂虾"],
        "category": "other",
        "cover_key": "",
        "summary": "不是虾也不是螳螂，一对“拳头”能打出超快的一击。",
        "look": "身体扁平多节，前肢像小锤子，眼睛能转。",
        "habitat": "潮间带泥沙底、浅海",
        "observe_tip": "看它怎么用前肢捕食，很厉害但别逗它。",
        "safety_tip": "前肢击打很疼，别用手抓。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_prawn",
        "name": "对虾",
        "aka": ["中国对虾", "明虾"],
        "category": "other",
        "cover_key": "",
        "summary": "半透明的小虾，游起来一弓一弓地弹。",
        "look": "身体透明带淡青色，触须细长，多对脚。",
        "habitat": "浅海、潮间带水域",
        "observe_tip": "潮池里看到透明小虾就是它，静看它游泳。",
        "safety_tip": "别去捞，容易伤到它。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_horseshoe_crab",
        "name": "中华鲎",
        "aka": ["鲎", "马蹄蟹"],
        "category": "other",
        "cover_key": "",
        "summary": "长得像古代武士的盾牌，是活化石，现在是保护动物。",
        "look": "身体像马蹄，拖着一条长长的剑尾。",
        "habitat": "近海沙滩、浅海泥底",
        "observe_tip": "请远远看看就好，它是国家保护动物。",
        "safety_tip": "保护动物，不要触碰、不要捡走。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": True,
    },
    # ===== 藻类 =====
    {
        "id": "sp_kelp",
        "name": "海带",
        "aka": ["昆布", "海带菜"],
        "category": "algae",
        "cover_key": "",
        "summary": "长长的大叶子海藻，是海底的“大森林”。",
        "look": "叶片又长又宽，褐绿色，有光泽。",
        "habitat": "潮下带礁石",
        "observe_tip": "退潮后海边漂来的海带可以看看，别捡回去煮。",
        "safety_tip": "海带表面滑，别踩上去。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_wakame",
        "name": "裙带菜",
        "aka": ["海芥菜", "若布"],
        "category": "algae",
        "cover_key": "",
        "summary": "叶片带“小裙子边”，退潮后常挂在礁石上。",
        "look": "叶片中间有主脉，边缘皱皱的像裙摆。",
        "habitat": "潮间带下部、潮下带礁石",
        "observe_tip": "摸一摸它滑滑的，闻一闻有海腥味。",
        "safety_tip": "湿滑，踩稳了再观察。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_laver",
        "name": "紫菜",
        "aka": ["海苔", "甘紫菜"],
        "category": "algae",
        "cover_key": "",
        "summary": "薄薄的一片紫红色海藻，晒干就是吃的海苔。",
        "look": "叶片薄如纸，紫红或褐紫，贴生在礁石上。",
        "habitat": "高潮带礁石",
        "observe_tip": "看它在阳光下泛着紫红光泽。",
        "safety_tip": "别在湿滑的礁石上乱跑。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_sargassum",
        "name": "马尾藻",
        "aka": ["海蒿子", "海藻"],
        "category": "algae",
        "cover_key": "",
        "summary": "一串串长着“小气球”的海藻，漂在水里像小树。",
        "look": "枝干细长，长着许多小气囊和细叶。",
        "habitat": "潮间带礁石、浅海",
        "observe_tip": "捏一捏上面的小气囊，能帮它浮起来。",
        "safety_tip": "别缠绕在身上玩。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_agar",
        "name": "石花菜",
        "aka": ["琼脂菜", "洋菜"],
        "category": "algae",
        "cover_key": "",
        "summary": "紫红色的细枝海藻，煮了会变成“果冻”。",
        "look": "枝条细密，紫红或暗红，一簇簇长在礁石上。",
        "habitat": "潮间带礁石",
        "observe_tip": "看看它密密麻麻的细枝，像小树枝。",
        "safety_tip": "别踩，礁石湿滑。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    # ===== 鱼类 =====
    {
        "id": "sp_mudskipper",
        "name": "弹涂鱼",
        "aka": ["跳跳鱼", "泥猴"],
        "category": "fish",
        "cover_key": "",
        "summary": "会“走路”的鱼，退潮后在泥滩上蹦蹦跳跳。",
        "look": "眼睛鼓鼓长在头顶，胸鳍像小短腿。",
        "habitat": "泥滩、河口滩涂",
        "observe_tip": "远远看它用胸鳍“爬行”、竖背鳍争地盘。",
        "safety_tip": "别去追，滩涂很泥泞容易陷脚。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_pufferfish",
        "name": "河豚",
        "aka": ["气泡鱼", "河鲀"],
        "category": "fish",
        "cover_key": "",
        "summary": "一生气就鼓成一个小球，但身上有毒不能碰。",
        "look": "身体圆鼓鼓，受惊会胀大，肚皮有尖刺。",
        "habitat": "近海、河口",
        "observe_tip": "潮池里偶尔能见到，看看就好。",
        "safety_tip": "河豚有剧毒，绝不能徒手抓或吃。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_seahorse",
        "name": "海马",
        "aka": ["龙落子", "马头鱼"],
        "category": "fish",
        "cover_key": "",
        "summary": "头像小马，尾巴会卷着海藻，是国家保护动物。",
        "look": "头像马，身体直立，尾巴细长能卷。",
        "habitat": "浅海海草床、珊瑚礁",
        "observe_tip": "请远远看看，别打扰它。",
        "safety_tip": "保护动物，不要触碰、不要捞。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": True,
    },
    # ===== 其他无脊椎动物 =====
    {
        "id": "sp_sea_urchin",
        "name": "海胆",
        "aka": ["刺锅子", "海刺猬"],
        "category": "other",
        "cover_key": "",
        "summary": "浑身是刺的“小刺猬”，刺底下藏着好吃的黄。",
        "look": "圆球状，满身硬刺，有的紫色有的黑。",
        "habitat": "潮间带礁石缝、潮池",
        "observe_tip": "看它慢慢移动的刺，别用手碰。",
        "safety_tip": "刺很尖会扎人，千万别赤脚踩。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_sea_cucumber",
        "name": "海参",
        "aka": ["海黄瓜", "刺参"],
        "category": "other",
        "cover_key": "",
        "summary": "软软的一条，慢慢在海底爬，一受惊会吐内脏。",
        "look": "身体圆柱形、软软的有肉刺，像根黄瓜。",
        "habitat": "潮下带礁石、泥沙底",
        "observe_tip": "它爬得特别慢，安静看就好。",
        "safety_tip": "别戳它，受惊吐内脏会伤身体。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_jellyfish",
        "name": "海蜇",
        "aka": ["水母", "海月水母"],
        "category": "other",
        "cover_key": "",
        "summary": "透明的小伞在水里一张一合，触手会蜇人。",
        "look": "伞状半透明，下面垂着细长触手。",
        "habitat": "近海、浅海水域",
        "observe_tip": "漂到沙滩上的水母看看就行，别碰。",
        "safety_tip": "触手会蜇人，不要用手碰，死水母也蜇人。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_sea_hare",
        "name": "海兔",
        "aka": ["雨虎", "海蛞蝓"],
        "category": "other",
        "cover_key": "",
        "summary": "软软滑滑像小兔子，一受惊会喷紫色“墨水”。",
        "look": "身体柔软无壳，头上有两对触角像兔耳朵。",
        "habitat": "潮间带海藻间",
        "observe_tip": "看它吃海藻的样子，特别可爱。",
        "safety_tip": "轻轻观察，别把它拎出水面。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_nudibranch",
        "name": "海蛞蝓",
        "aka": ["海牛", "海兔蛞蝓"],
        "category": "other",
        "cover_key": "",
        "summary": "海底最会“穿花衣”的小精灵，五颜六色。",
        "look": "身体柔软、颜色鲜艳，背上有漂亮的突起。",
        "habitat": "潮间带礁石、海藻上",
        "observe_tip": "颜色越鲜艳越要远远看，别碰。",
        "safety_tip": "有些海蛞蝓有毒，别用手摸。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_ragworm",
        "name": "沙蚕",
        "aka": ["海虫", "岩虫"],
        "category": "other",
        "cover_key": "",
        "summary": "藏在泥沙里的小蠕虫，是钓鱼的好饵料。",
        "look": "身体细长分节，两侧有细小的刚毛。",
        "habitat": "潮间带泥沙底、礁石下",
        "observe_tip": "翻沙子时看到它钻进去，动作很快。",
        "safety_tip": "别徒手抓，刚毛可能扎手。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_sea_squirt",
        "name": "海鞘",
        "aka": ["海奶子", "被囊动物"],
        "category": "other",
        "cover_key": "",
        "summary": "长在礁石上的“小水袋”，一碰会喷出一股水。",
        "look": "像个小口袋，有进出水口，颜色各异。",
        "habitat": "潮间带礁石、码头桩基",
        "observe_tip": "轻轻碰一下，看它喷水，很有意思。",
        "safety_tip": "别用力捏，会伤到它。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_octopus",
        "name": "章鱼",
        "aka": ["八爪鱼", "八带"],
        "category": "other",
        "cover_key": "",
        "summary": "八条腕的聪明家伙，会变色还会喷墨逃跑。",
        "look": "身体圆软，八条腕上有吸盘，眼睛很灵。",
        "habitat": "潮间带礁石缝、潮池",
        "observe_tip": "潮池里的小章鱼，静看它变色躲藏。",
        "safety_tip": "别抓，它会咬，也会喷墨。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_cuttlefish",
        "name": "乌贼",
        "aka": ["墨鱼", "墨斗鱼"],
        "category": "other",
        "cover_key": "",
        "summary": "身体里藏一块白色“墨鱼骨”，会喷墨汁。",
        "look": "身体椭圆，两侧有鳍，肚里有白骨头。",
        "habitat": "浅海、近海",
        "observe_tip": "海边捡到的白色墨鱼骨，是它留下的。",
        "safety_tip": "观察为主，别打扰活的乌贼。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_sand_dollar",
        "name": "海钱",
        "aka": ["沙币", "沙钱"],
        "category": "other",
        "cover_key": "",
        "summary": "扁扁圆圆的像枚硬币，是海胆的近亲。",
        "look": "身体扁平圆形，表面有五瓣花纹。",
        "habitat": "潮下带沙滩、浅海",
        "observe_tip": "捡到白色空壳可以看看上面的花纹。",
        "safety_tip": "活的海钱要放回海里。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_sea_slater",
        "name": "海蟑螂",
        "aka": ["海虱", "海岸水虱"],
        "category": "other",
        "cover_key": "",
        "summary": "礁石缝里成群的小虫，跑得飞快，其实不是蟑螂。",
        "look": "身体扁平灰褐，有好多腿，受惊四散跑。",
        "habitat": "高潮带礁石缝、岩岸",
        "observe_tip": "看它们成群在礁石上找海藻吃。",
        "safety_tip": "不咬人，但别抓，它们会装死或乱跑。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-27",
        "protected": False,
    },
    {
        "id": "sp_blood_clam",
        "name": "毛蚶",
        "aka": ["血蛤", "毛蛤", "赤贝"],
        "category": "shell",
        "cover_key": "",
        "summary": "壳上长着细毛的小贝，煮熟会流“红汁”，是北方赶海的常见收获。",
        "look": "壳厚鼓鼓的，表面布满细密的毛状凸起，里面橘红色。",
        "habitat": "潮间带泥沙底、滩涂",
        "observe_tip": "退潮后沙滩上能看到半埋在沙里的毛蚶，轻轻拨开沙子找。",
        "safety_tip": "壳边的毛刺会扎手，捡的时候别太用力。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-28",
        "protected": False,
    },
    {
        "id": "sp_babylon_snail",
        "name": "花螺",
        "aka": ["东风螺", "黄螺"],
        "category": "shell",
        "cover_key": "",
        "summary": "壳上带漂亮斑纹的螺，常在浅滩慢慢爬行。",
        "look": "壳橄榄形，表面有黄褐色斑块花纹，壳口较大。",
        "habitat": "潮间带泥沙底、浅海",
        "observe_tip": "看它在沙面上留下的爬痕，能判断它往哪边去了。",
        "safety_tip": "别徒手摸壳内软体，有些螺可能有毒。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-28",
        "protected": False,
    },
    {
        "id": "sp_spoon_worm",
        "name": "海肠",
        "aka": ["单环刺螠", "海鸡子"],
        "category": "other",
        "cover_key": "",
        "summary": "长得像一根粉红色的“小香肠”，埋在海滩泥沙里。",
        "look": "身体圆柱形、粉红或肉色，前端有几根短须。",
        "habitat": "潮间带泥沙底、滩涂",
        "observe_tip": "用铲子轻轻挖滩涂，能看到它飞快缩回洞里。",
        "safety_tip": "挖的时候轻一点，别破坏滩涂。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-28",
        "protected": False,
    },
    {
        "id": "sp_goose_barnacle",
        "name": "狗爪螺",
        "aka": ["龟足", "佛手螺", "笔架"],
        "category": "shell",
        "cover_key": "",
        "summary": "像乌龟的小爪子一样扒在礁石上的甲壳动物，一簇簇生长。",
        "look": "顶端是几片灰白壳板，下面是黑褐色的软柄，成簇附在岩缝里。",
        "habitat": "潮间带礁石、岩缝",
        "observe_tip": "退潮后看它们一簇簇牢牢扒在礁石上，像小爪子。",
        "safety_tip": "壳边缘锋利，别硬抠，会划伤手。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-08-28",
        "protected": False,
    },
    {
        "id": "sp_tide",
        "name": "潮汐",
        "aka": ["海潮", "涨落潮"],
        "category": "physical",
        "cover_key": "",
        "summary": "海水在月球和太阳引力下周期性涨落，是最容易在海边观察到的海洋现象。",
        "look": "多数海岸一天有两次涨落；朔望时潮差最大（大潮），上下弦时潮差最小（小潮）。",
        "habitat": "全球海岸；海湾、河口和狭窄地形会放大或削弱潮差。",
        "observe_tip": "连续几天记录同一块礁石被淹没的时间，会发现涨潮每天大约推迟 50 分钟。",
        "safety_tip": "赶海前先看当天潮汐表，涨潮前及时离开滩涂和礁石。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_ocean_current",
        "name": "洋流",
        "aka": ["海流", "大洋洋流"],
        "category": "physical",
        "cover_key": "",
        "summary": "海水在全球尺度上沿着相对固定的路径流动，像海洋里的河流。",
        "look": "主要由风、海水密度差和地球自转共同驱动；表层流和深层流方向常常不同。",
        "habitat": "各大洋；暖流把热量从低纬带向高纬，寒流则相反。",
        "observe_tip": "观察漂流物、海雾和沿岸水温变化，能间接感受到洋流的影响。",
        "safety_tip": "离岸流很危险，游泳时不要逆流硬游，应向两侧脱离。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_wave",
        "name": "海浪",
        "aka": ["波浪", "涌浪"],
        "category": "physical",
        "cover_key": "",
        "summary": "风把能量传给海面形成波浪，波浪可以跨越很远的距离传播。",
        "look": "风浪受当地风影响，涌浪则来自远处的风暴；接近浅水时波高增大并破碎。",
        "habitat": "所有海面；礁石岸、沙滩和防波堤会以不同方式消耗波浪能量。",
        "observe_tip": "看浪花破碎的位置和周期，能判断海底地形和当天海况。",
        "safety_tip": "台风或大风后不要靠近礁石边缘，疯狗浪可能突然打上来。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_thermocline",
        "name": "温跃层",
        "aka": ["温度跃层"],
        "category": "physical",
        "cover_key": "",
        "summary": "海水温度在某一深度突然快速下降的水层，像海洋里的一层隐形隔板。",
        "look": "上层温暖、下层寒冷，中间几十米内温度骤变；季节和纬度会改变它的深度。",
        "habitat": "中低纬度外海、深水湖和部分海湾；浅水潮间带通常不明显。",
        "observe_tip": "潜水或看海洋调查数据时，温跃层附近常能看到鱼群和营养盐的集中变化。",
        "safety_tip": "温跃层会干扰声呐和潜水员的判断，水下活动要听从专业人员安排。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_salinity",
        "name": "海水盐度",
        "aka": ["盐度", "海水含盐量"],
        "category": "physical",
        "cover_key": "",
        "summary": "一公斤海水里溶解的盐类总量，决定海水的密度、浮力和结冰温度。",
        "look": "外海通常在 3.5% 左右；蒸发强、降水少的海域更高，河口和近岸会明显变淡。",
        "habitat": "全球海洋；河口、潟湖和封闭海湾的盐度变化最大。",
        "observe_tip": "盐度是海水的基本物理性质；观察时不要直接用嘴尝海水。",
        "safety_tip": "盐度骤变会影响海洋生物，参观养殖区时不要随意投喂或放生。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_argo_float",
        "name": "Argo 浮标",
        "aka": ["剖面浮标", "Argo"],
        "category": "tech",
        "cover_key": "",
        "summary": "自动在海里上浮下沉、测量温度和盐度的漂流浮标，是全球海洋观测的主力。",
        "look": "约一人高的圆柱体，靠改变浮力下潜到约 2000 米，再上浮把数据传回卫星。",
        "habitat": "全球大洋；数千个浮标组成 Argo 阵列，常年无人值守。",
        "observe_tip": "Argo 数据公开可查，可以看看你关注的海域最近一次剖面是什么时候。",
        "safety_tip": "海上遇到浮标不要拖拽或拆解，它们承担着长期观测任务。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_rov",
        "name": "水下机器人",
        "aka": ["ROV", "遥控潜水器"],
        "category": "tech",
        "cover_key": "",
        "summary": "通过缆线由母船遥控的水下机器人，能替人进入深海完成观察和取样。",
        "look": "方形框架、推进器、摄像机和机械臂；缆线同时传输电力、视频和控制信号。",
        "habitat": "深海热液、沉船、海底管线和水下工程现场。",
        "observe_tip": "看科考直播时留意机械臂如何取样，以及灯光在深水里能照多远。",
        "safety_tip": "ROV 作业水域会设警戒，船只和潜水员不要靠近缆线和推进器。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_multibeam_sonar",
        "name": "多波束声呐",
        "aka": ["多波束测深", "海底测绘"],
        "category": "tech",
        "cover_key": "",
        "summary": "向海底发射一片扇形声波，同时测出许多点的深度，用来绘制海底地形图。",
        "look": "船底换能器发射声波，回收时间换算成深度；多个波束拼成一条宽幅测带。",
        "habitat": "科考船、测量船和港口航道；从浅海大陆架到深海海沟都能用。",
        "observe_tip": "海图上的等深线和海底山脊，很多就是多波束测出来的。",
        "safety_tip": "声呐对海洋哺乳动物有影响，调查船会按规定避开繁殖和迁徙区域。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_ocean_satellite",
        "name": "海洋卫星",
        "aka": ["海洋遥感卫星"],
        "category": "tech",
        "cover_key": "",
        "summary": "从太空监测海面高度、风场、温度和颜色的卫星，让人类每天看到整片海洋。",
        "look": "不同传感器分工：高度计测海平面，散射计测风，水色仪测浮游植物。",
        "habitat": "近地轨道；一颗卫星每天可覆盖全球大部分海域。",
        "observe_tip": "台风路径、赤潮和海水温度图背后，很多数据都来自海洋卫星。",
        "safety_tip": "卫星数据是预报工具，不能替代现场潮汐表和海事部门的安全通知。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_artificial_reef",
        "name": "人工鱼礁",
        "aka": ["鱼礁", "海洋牧场"],
        "category": "tech",
        "cover_key": "",
        "summary": "把设计过的礁体投放到海底，为鱼类和附着生物提供躲避、附着和觅食的空间。",
        "look": "混凝土、石块或钢架结构，表面会逐渐长满藻类、藤壶和珊瑚。",
        "habitat": "近岸浅海、退化礁区、海洋牧场和增殖放流海域。",
        "observe_tip": "观察鱼礁区时，别只数大鱼，也看看礁体表面附着了哪些小生物。",
        "safety_tip": "鱼礁区可能有渔网和锚链，潜水、垂钓前先了解当地管理规定。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_whale",
        "name": "鲸",
        "aka": ["鲸鱼", "海兽"],
        "category": "other_marine",
        "cover_key": "",
        "summary": "生活在海洋里的哺乳动物，用肺呼吸，靠声音交流和导航。",
        "look": "须鲸用鲸须滤食小鱼小虾，齿鲸用牙齿捕食；头顶有呼吸孔，尾鳍水平摆动。",
        "habitat": "从近岸到开阔大洋；很多种类会进行长距离季节性迁徙。",
        "observe_tip": "观鲸要保持距离，看到喷潮、尾鳍或跃身时不要追船。",
        "safety_tip": "鲸是受保护动物，禁止靠近、投喂、触摸和追逐。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": True,
    },
    {
        "id": "sp_sea_turtle",
        "name": "海龟",
        "aka": ["绿海龟", "海龟"],
        "category": "other_marine",
        "cover_key": "",
        "summary": "一生大部分时间在海里生活的爬行动物，要回到沙滩上产卵。",
        "look": "流线型背甲、桨状四肢，不能把头缩进壳里；不同种类背甲花纹不同。",
        "habitat": "热带和亚热带海域、海草床、珊瑚礁和沙滩。",
        "observe_tip": "夜间看海龟上岸产卵要保持安静和距离，不能打灯直照。",
        "safety_tip": "海龟受保护，禁止触摸、骑乘、投喂和采集龟卵。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": True,
    },
    {
        "id": "sp_seabird",
        "name": "海鸟",
        "aka": ["海鸥", "鸻鹬"],
        "category": "other_marine",
        "cover_key": "",
        "summary": "长期在海岸和海上活动的鸟类，能用盐腺排出喝进去的多余盐分。",
        "look": "有蹼或长腿，羽毛防水；不同种类分别在崖壁、沙滩或岛上筑巢。",
        "habitat": "海岸、海岛、礁石和开阔海面；迁徙季节会经过很长的海岸线。",
        "observe_tip": "繁殖季不要靠近巢区，亲鸟受惊弃巢会让蛋和雏鸟暴露在烈日下。",
        "safety_tip": "不要投喂海鸥，人类食物会改变它们的觅食习惯并带来疾病风险。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_hydrothermal_vent",
        "name": "深海热液",
        "aka": ["热液喷口", "黑烟囱"],
        "category": "other_marine",
        "cover_key": "",
        "summary": "海底裂缝喷出高温富含矿物的热水，周围生活着不靠阳光的独特生态系统。",
        "look": "喷口附近常见黑烟囱和白色菌席；管虫、盲虾、贻贝成片聚集。",
        "habitat": "板块活动活跃的深海海底，通常在水深数百到数千米。",
        "observe_tip": "热液生态靠化能合成而不是光合作用，是寻找地外生命的重要参考。",
        "safety_tip": "热液区水温极高，只有专业深潜器才能靠近，普通人不要尝试。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
    {
        "id": "sp_plankton",
        "name": "浮游生物",
        "aka": ["浮游植物", "浮游动物"],
        "category": "other_marine",
        "cover_key": "",
        "summary": "随水流漂动的微小生物，是海洋食物网的基础，也贡献了大量氧气。",
        "look": "浮游植物多为单细胞藻类，浮游动物包括桡足类、水母幼体等；多数肉眼难辨。",
        "habitat": "从海面到深海都有；营养盐和光照决定浮游植物的分布。",
        "observe_tip": "夜晚海面的荧光很多来自甲藻等浮游生物，用手电照一照能看到。",
        "safety_tip": "有些浮游藻类会形成赤潮，贝类毒素风险高时不要采食。",
        "source": "追潮记团队整理",
        "reviewed_at": "2026-11-11",
        "protected": False,
    },
]

# 官方图鉴封面图对象 key（对应 data/uploads/pictorial/ 下的文件）
SPECIES_COVERS = {
    "sp_barnacle": "pictorial/sp_barnacle.jpg",
    "sp_oyster": "pictorial/sp_oyster.jpg",
    "sp_limpet": "pictorial/sp_limpet.jpg",
    "sp_sea_lettuce": "pictorial/sp_sea_lettuce.jpg",
    "sp_hermit_crab": "pictorial/sp_hermit_crab.jpg",
    "sp_sea_anemone": "pictorial/sp_sea_anemone.jpg",
    "sp_starfish": "pictorial/sp_starfish.jpg",
    "sp_mussel": "pictorial/sp_mussel.jpg",
    "sp_clam": "pictorial/sp_clam.jpg",
    "sp_scallop": "pictorial/sp_scallop.jpg",
    "sp_razor_clam": "pictorial/sp_razor_clam.jpg",
    "sp_abalone": "pictorial/sp_abalone.jpg",
    "sp_whelk": "pictorial/sp_whelk.jpg",
    "sp_cowry": "pictorial/sp_cowry.jpg",
    "sp_chiton": "pictorial/sp_chiton.jpg",
    "sp_moon_snail": "pictorial/sp_moon_snail.jpg",
    "sp_fiddler_crab": "pictorial/sp_fiddler_crab.jpg",
    "sp_ghost_crab": "pictorial/sp_ghost_crab.jpg",
    "sp_rock_crab": "pictorial/sp_rock_crab.jpg",
    "sp_swimming_crab": "pictorial/sp_swimming_crab.jpg",
    "sp_mantis_shrimp": "pictorial/sp_mantis_shrimp.jpg",
    "sp_prawn": "pictorial/sp_prawn.jpg",
    "sp_horseshoe_crab": "pictorial/sp_horseshoe_crab.jpg",
    "sp_kelp": "pictorial/sp_kelp.jpg",
    "sp_wakame": "pictorial/sp_wakame.jpg",
    "sp_laver": "pictorial/sp_laver.jpg",
    "sp_sargassum": "pictorial/sp_sargassum.jpg",
    "sp_agar": "pictorial/sp_agar.jpg",
    "sp_mudskipper": "pictorial/sp_mudskipper.jpg",
    "sp_pufferfish": "pictorial/sp_pufferfish.jpg",
    "sp_seahorse": "pictorial/sp_seahorse.jpg",
    "sp_sea_urchin": "pictorial/sp_sea_urchin.jpg",
    "sp_sea_cucumber": "pictorial/sp_sea_cucumber.jpg",
    "sp_jellyfish": "pictorial/sp_jellyfish.jpg",
    "sp_sea_hare": "pictorial/sp_sea_hare.jpg",
    "sp_nudibranch": "pictorial/sp_nudibranch.jpg",
    "sp_ragworm": "pictorial/sp_ragworm.jpg",
    "sp_sea_squirt": "pictorial/sp_sea_squirt.jpg",
    "sp_octopus": "pictorial/sp_octopus.jpg",
    "sp_cuttlefish": "pictorial/sp_cuttlefish.jpg",
    "sp_sand_dollar": "pictorial/sp_sand_dollar.jpg",
    "sp_sea_slater": "pictorial/sp_sea_slater.jpg",
    "sp_blood_clam": "pictorial/sp_blood_clam.jpg",
    "sp_babylon_snail": "pictorial/sp_babylon_snail.jpg",
    "sp_spoon_worm": "pictorial/sp_spoon_worm.jpg",
    "sp_goose_barnacle": "pictorial/sp_goose_barnacle.jpg",
    "sp_korean_rockfish": "pictorial/sp_korean_rockfish.jpg",
}

QUIZZES = [
    {"id": "quiz_tide_intro", "title": "潮汐入门", "cover_key": ""},
    {"id": "quiz_intertidal", "title": "潮间带生物", "cover_key": ""},
    {"id": "quiz_safety", "title": "安全观察", "cover_key": ""},
]

QUIZ_QUESTIONS = [
    # 潮汐入门
    {
        "id": "qq_tide_1", "quiz_id": "quiz_tide_intro", "sort": 1,
        "stem": "退潮时，海水会向哪个方向退？",
        "options": [{"id": "A", "text": "向岸边涌来"}, {"id": "B", "text": "向海里退去"}, {"id": "C", "text": "原地不动"}],
        "correct_option_id": "B",
        "explain": "退潮时海水从岸边向海中退去，水位下降，露出潮间带。",
    },
    {
        "id": "qq_tide_2", "quiz_id": "quiz_tide_intro", "sort": 2,
        "stem": "一天里通常有几次涨潮、几次退潮？",
        "options": [{"id": "A", "text": "一次涨潮、一次退潮"}, {"id": "B", "text": "两次涨潮、两次退潮"}, {"id": "C", "text": "三次涨潮、三次退潮"}],
        "correct_option_id": "B",
        "explain": "大多数沿海地区一天有两次涨潮和两次退潮，约 12 小时一个周期。",
    },
    {
        "id": "qq_tide_3", "quiz_id": "quiz_tide_intro", "sort": 3,
        "stem": "下面哪个对地球潮汐的影响更大？",
        "options": [{"id": "A", "text": "月亮"}, {"id": "B", "text": "太阳"}, {"id": "C", "text": "星星"}],
        "correct_option_id": "A",
        "explain": "月亮离地球近，对潮汐的影响比太阳更大。",
    },
    {
        "id": "qq_tide_4", "quiz_id": "quiz_tide_intro", "sort": 4,
        "stem": "赶海观察潮间带生物，最好选在什么时候？",
        "options": [{"id": "A", "text": "涨潮最高的时候"}, {"id": "B", "text": "退潮后、涨潮前"}, {"id": "C", "text": "刮大风的时候"}],
        "correct_option_id": "B",
        "explain": "退潮后潮间带露出来，能看到更多生物，涨潮前又相对安全。",
    },
    {
        "id": "qq_tide_5", "quiz_id": "quiz_tide_intro", "sort": 5,
        "stem": "涨潮时，海边的水位会怎么样？",
        "options": [{"id": "A", "text": "升高，向岸边推进"}, {"id": "B", "text": "降低，向海里退"}, {"id": "C", "text": "保持不变"}],
        "correct_option_id": "A",
        "explain": "涨潮时海水向岸边推进，水位升高，会淹没潮间带。",
    },
    # 潮间带生物
    {
        "id": "qq_bi_1", "quiz_id": "quiz_intertidal", "sort": 1,
        "stem": "藤壶通常住在哪里？",
        "options": [{"id": "A", "text": "礁石表面"}, {"id": "B", "text": "大海深处"}, {"id": "C", "text": "天上"}],
        "correct_option_id": "A",
        "explain": "藤壶牢牢附着在潮间带的礁石、码头、船底上。",
    },
    {
        "id": "qq_bi_2", "quiz_id": "quiz_intertidal", "sort": 2,
        "stem": "寄居蟹的“家”是什么？",
        "options": [{"id": "A", "text": "自己盖的洞"}, {"id": "B", "text": "捡来的空螺壳"}, {"id": "C", "text": "树叶"}],
        "correct_option_id": "B",
        "explain": "寄居蟹会钻进空螺壳里当房子，长大了再换更大的壳。",
    },
    {
        "id": "qq_bi_3", "quiz_id": "quiz_intertidal", "sort": 3,
        "stem": "海葵是用什么来捕食的？",
        "options": [{"id": "A", "text": "触手"}, {"id": "B", "text": "脚"}, {"id": "C", "text": "翅膀"}],
        "correct_option_id": "A",
        "explain": "海葵伸出带黏性的触手，抓住路过的小浮游生物送进嘴里。",
    },
    {
        "id": "qq_bi_4", "quiz_id": "quiz_intertidal", "sort": 4,
        "stem": "观察潮间带生物时，下面哪种做法是对的？",
        "options": [{"id": "A", "text": "把它们抠下来带回家"}, {"id": "B", "text": "只用眼睛看，不伤害"}, {"id": "C", "text": "用棍子戳"}],
        "correct_option_id": "B",
        "explain": "观察生物要做到“只看不碰”，保护它们也保护自己。",
    },
    {
        "id": "qq_bi_5", "quiz_id": "quiz_intertidal", "sort": 5,
        "stem": "海星通常有几条腕？",
        "options": [{"id": "A", "text": "两条"}, {"id": "B", "text": "五条"}, {"id": "C", "text": "十条"}],
        "correct_option_id": "B",
        "explain": "大多数海星有五条腕，少数种类会更多。",
    },
    # 安全观察
    {
        "id": "qq_safe_1", "quiz_id": "quiz_safety", "sort": 1,
        "stem": "在湿滑的礁石上，下面哪种做法最安全？",
        "options": [{"id": "A", "text": "奔跑打闹"}, {"id": "B", "text": "站稳慢走，不奔跑"}, {"id": "C", "text": "光脚跳来跳去"}],
        "correct_option_id": "B",
        "explain": "礁石湿滑，要穿防滑鞋、站稳慢走，绝不奔跑。",
    },
    {
        "id": "qq_safe_2", "quiz_id": "quiz_safety", "sort": 2,
        "stem": "发现开始涨潮了，应该怎么做？",
        "options": [{"id": "A", "text": "继续玩"}, {"id": "B", "text": "提前离开水边"}, {"id": "C", "text": "往更深处走"}],
        "correct_option_id": "B",
        "explain": "涨潮很快，看到海水上涨要马上离开水边，回到安全的地方。",
    },
    {
        "id": "qq_safe_3", "quiz_id": "quiz_safety", "sort": 3,
        "stem": "遇到没见过的海洋生物，正确的做法是？",
        "options": [{"id": "A", "text": "远远看看，不碰"}, {"id": "B", "text": "抓起来研究"}, {"id": "C", "text": "用脚踩"}],
        "correct_option_id": "A",
        "explain": "不认识的生物可能有毒或会伤人，远远观察最安全。",
    },
    {
        "id": "qq_safe_4", "quiz_id": "quiz_safety", "sort": 4,
        "stem": "未成年人去赶海，最应该做到什么？",
        "options": [{"id": "A", "text": "有大人陪同"}, {"id": "B", "text": "自己去"}, {"id": "C", "text": "和小伙伴单独去"}],
        "correct_option_id": "A",
        "explain": "未成年人赶海一定要有大人陪同，安全第一。",
    },
]

_STATIC_BASE = "https://www.blueakaiwu.cn/api/v1/static/assets"

# 勋章图的版本号：静态资源带 `Cache-Control: max-age=86400`，客户端按 URL 缓存 24 小时。
# **换图之后必须把这个版本号 +1**，否则老用户 24 小时内还会看到旧图。
_MEDAL_IMG_V = "?v=2"

# 首页活动轮播（图片走静态资源同域，返回完整 URL）
# layout：card = 左侧文字 + 右侧小插图；banner = 整幅宽图铺满卡片
ACTIVITIES = [
    {
        "id": "act_deepblue_09",
        "tag": "科普",
        "title": "深蓝百万里",
        "desc": "探秘深海生物的世界",
        # theme=banner：前端把它渲染成「图片当背景 + 文字叠在上面」的横幅卡
        # （与前端同学上传的实现一致，不再用 layout 字段区分）
        "theme": "banner",
        "image": f"{_STATIC_BASE}/home/home-deepblue.png",
        # 站内页。原本想指向公众号文章，但 web-view 加载 mp.weixin.qq.com 需要
        # 「关联公众号」，真机过不了，已放弃外链方案。
        "url": "/pages/knowledge/knowledge",
        "sort": 0,
    },
    {
        "id": "act_science_09",
        "tag": "科普",
        "title": "潮间带科普课",
        "desc": "认识小螃蟹和贝类朋友",
        "theme": "teal",
        "image": f"{_STATIC_BASE}/home/home-fish.png",
        "url": "/pages/knowledge/knowledge",
        "sort": 1,
    },
    {
        "id": "act_study_09",
        "tag": "研学",
        "title": "赶海研学活动",
        "desc": "带上装备，一起去海边观察",
        "theme": "yellow",
        "image": f"{_STATIC_BASE}/home/home-nearby.png",
        "url": "/pages/spots/spots",
        "sort": 2,
    },
]

COMMUNITY_TOPICS = [
    {"id": "topic_lowtide", "name": "退潮观察", "sort": 1},
    {"id": "topic_rocky", "name": "礁石生物", "sort": 2},
    {"id": "topic_safety", "name": "安全赶海", "sort": 3},
    {"id": "topic_gear", "name": "装备分享", "sort": 4},
    {"id": "topic_wiki", "name": "图鉴对照", "sort": 5},
]

KNOWLEDGE = [
    {"id": "k_intertidal", "title": "潮间带是什么", "summary": "涨潮被淹、退潮露出来的那一带。", "tags": ["基础", "观察"], "sort": 1,
     "body": "潮间带是高潮线和低潮线之间的地带。涨潮时它在水下，退潮后石头、沙滩会露出来。很多小生物就住在这里，所以赶海观察，其实是在看潮间带的邻居。"},
    {"id": "k_ebb", "title": "为什么退潮更好观察", "summary": "水位下去，礁石缝更容易看见。", "tags": ["潮汐", "观察"], "sort": 2,
     "body": "退潮后水位降低，贴在礁石上的藤壶、牡蛎和潮沟里的小生物更容易被看见。不要等到涨潮再往水边走。看完要提前离开，给涨潮留出时间。"},
    {"id": "k_safety_rise", "title": "涨潮来了怎么办", "summary": "提前离开水边，不要贪看。", "tags": ["安全"], "sort": 3,
     "body": "潮水上涨比想象中快，尤其是礁石区。出门前先看今日高潮时间。如果水面明显上涨、浪花打到刚才还能走的地方，立刻往岸上走，不要回头捡东西。"},
    {"id": "k_slippery", "title": "礁石为什么湿滑", "summary": "海藻和湿石头会让脚打滑。", "tags": ["安全", "装备"], "sort": 4,
     "body": "礁石上常有海藻和积水，看起来不陡也会滑。穿防滑鞋，走稳再伸手看。不要奔跑，不要单脚踩尖石头。小朋友必须有大人牵着。"},
    {"id": "k_leave", "title": "看见了不要带走", "summary": "观察可以，把家搬回家不行。", "tags": ["保护", "观察"], "sort": 5,
     "body": "藤壶、海星、螺类都是潮间带的居民。把它们装进袋子，它们很难活下来，礁石上的邻居也会变少。用眼睛和图鉴对照就好，拍完照片把石头轻轻放回原处。"},
    {"id": "k_barnacle", "title": "礁石上的小房子", "summary": "那常常是藤壶，不是石头长出来的。", "tags": ["生物", "图鉴"], "sort": 6,
     "body": "退潮后礁石上密密的白色小房子，多半是藤壶。它们用壳粘在石头上滤食。不要用工具撬。对照图鉴看形状和排列，比凭感觉猜更准。"},
    {"id": "k_accompany", "title": "为什么要大人陪同", "summary": "海边有潮、有浪，也有湿滑。", "tags": ["安全", "适龄"], "sort": 7,
     "body": "赶海不是操场游戏。潮水、礁石和风浪都需要有人帮忙判断。小学中低年级请始终由大人陪同，不要独自走近水线。大人也要一起看潮汐时间。"},
    {"id": "k_gear", "title": "去海边带什么", "summary": "防滑、防晒、装水，比网兜更重要。", "tags": ["装备"], "sort": 8,
     "body": "优先带防滑鞋、防晒帽、饮用水和垃圾袋。小桶可以暂时盛一点海水观察，看完倒回海里。不推荐锋利工具。现场管理规定比装备清单更优先。"},
    {"id": "k_forecast", "title": "潮汐表只能当参考", "summary": "以海洋预报和现场警示为准。", "tags": ["潮汐", "安全"], "sort": 9,
     "body": "小程序里的潮高、涨退时间能帮你做计划，但不是保证。风力、风暴潮和景区封闭都会改变情况。出门前看官方预报，到了现场先看警示牌和工作人员指引。"},
    {"id": "k_observe", "title": "怎样才算认真观察", "summary": "蹲下来看，对照图鉴，记下时间。", "tags": ["观察", "图鉴"], "sort": 10,
     "body": "先看水位和安全，再选一块小范围慢慢看。记下大约时间、潮汐是涨还是退、生物长在石头上还是水洼里。不确定名字时，用拍照识别当线索，仍要打开图鉴再确认。"},
]

# 点亮地图：3 张图 × 6 个区块（map_url 留空，前端用本地底图）
LIGHT_MAPS = [
    {"id": "qingdao", "name": "青岛海岸", "map_url": "", "sort": 1, "regions": [
        {"id": "isles", "name": "离岛海域", "hint": "竹岔岛一带", "minLat": 35.96, "minLng": 120.30, "maxLat": 36.02, "maxLng": 120.38, "left": 0, "top": 0, "width": 34, "height": 52},
        {"id": "jiaozhou", "name": "胶州湾", "hint": "团岛到红岛沿岸", "minLat": 36.04, "minLng": 120.28, "maxLat": 36.16, "maxLng": 120.40, "left": 33, "top": 0, "width": 34, "height": 52},
        {"id": "qd_east", "name": "青岛东部海岸", "hint": "石老人到小麦岛一带", "minLat": 36.05, "minLng": 120.42, "maxLat": 36.12, "maxLng": 120.52, "left": 66, "top": 0, "width": 34, "height": 52},
        {"id": "west_coast", "name": "西海岸", "hint": "黄岛金沙滩一带", "minLat": 35.93, "minLng": 120.16, "maxLat": 36.02, "maxLng": 120.30, "left": 0, "top": 48, "width": 34, "height": 52},
        {"id": "jimo", "name": "即墨滨海", "hint": "鳌山卫到田横岛", "minLat": 36.32, "minLng": 120.62, "maxLat": 36.48, "maxLng": 120.80, "left": 33, "top": 48, "width": 34, "height": 52},
        {"id": "laoshan", "name": "崂山滨海", "hint": "崂山湾与仰口", "minLat": 36.12, "minLng": 120.54, "maxLat": 36.28, "maxLng": 120.72, "left": 66, "top": 48, "width": 34, "height": 52},
    ]},
    {"id": "weihai", "name": "威海海岸", "map_url": "", "sort": 2, "regions": [
        {"id": "liugong", "name": "刘公岛", "hint": "威海湾中的海岛", "minLat": 37.49, "minLng": 122.12, "maxLat": 37.52, "maxLng": 122.20, "left": 0, "top": 0, "width": 34, "height": 52},
        {"id": "weihai_bay", "name": "威海湾", "hint": "国际海水浴场一带", "minLat": 37.48, "minLng": 122.04, "maxLat": 37.54, "maxLng": 122.14, "left": 33, "top": 0, "width": 34, "height": 52},
        {"id": "chengshantou", "name": "成山头", "hint": "天尽头东端", "minLat": 37.36, "minLng": 122.52, "maxLat": 37.42, "maxLng": 122.72, "left": 66, "top": 0, "width": 34, "height": 52},
        {"id": "huancui", "name": "环翠沿岸", "hint": "市区海边栈道", "minLat": 37.50, "minLng": 121.98, "maxLat": 37.56, "maxLng": 122.08, "left": 0, "top": 48, "width": 34, "height": 52},
        {"id": "rongcheng", "name": "荣成滨海", "hint": "天鹅湖到东褚岛", "minLat": 37.32, "minLng": 122.40, "maxLat": 37.40, "maxLng": 122.55, "left": 33, "top": 48, "width": 34, "height": 52},
        {"id": "shidao", "name": "石岛", "hint": "南部渔港海岸", "minLat": 36.86, "minLng": 122.40, "maxLat": 36.94, "maxLng": 122.48, "left": 66, "top": 48, "width": 34, "height": 52},
    ]},
    {"id": "rizhao", "name": "日照海岸", "map_url": "", "sort": 3, "regions": [
        {"id": "wanpingkou", "name": "万平口", "hint": "市区黄金海岸", "minLat": 35.40, "minLng": 119.54, "maxLat": 35.46, "maxLng": 119.62, "left": 0, "top": 0, "width": 34, "height": 52},
        {"id": "lighthouse", "name": "灯塔风景区", "hint": "日照灯塔附近", "minLat": 35.38, "minLng": 119.52, "maxLat": 35.43, "maxLng": 119.58, "left": 33, "top": 0, "width": 34, "height": 52},
        {"id": "taigong", "name": "太公岛", "hint": "近岸小岛一带", "minLat": 35.36, "minLng": 119.56, "maxLat": 35.40, "maxLng": 119.62, "left": 66, "top": 0, "width": 34, "height": 52},
        {"id": "jiangfeng", "name": "姜太公故里岸", "hint": "北部沿海", "minLat": 35.48, "minLng": 119.50, "maxLat": 35.56, "maxLng": 119.62, "left": 0, "top": 48, "width": 34, "height": 52},
        {"id": "donggang", "name": "东港海岸", "hint": "港口以南沙滩", "minLat": 35.34, "minLng": 119.48, "maxLat": 35.40, "maxLng": 119.56, "left": 33, "top": 48, "width": 34, "height": 52},
        {"id": "lanshan", "name": "岚山滨海", "hint": "南部岚山沿岸", "minLat": 35.08, "minLng": 119.30, "maxLat": 35.18, "maxLng": 119.42, "left": 66, "top": 48, "width": 34, "height": 52},
    ]},
]

MEDALS = [
    # ===== 众筹成就（设计同学交来的 13 个）=====
    # icon_key = 完整 URL（客户端把它当完整地址用）。这里先用现成的 badge 图占位，
    # 后续换成正式图案时只改这一行即可；_seed_missing 只插不改，所以要改已入库的
    # 成就得另外写一次同步（或手工改库）。
    {
        "id": "medal_6", "title": "蓝色青年行动", "display_title": "蓝色青年行动", "rarity": "common",
        "icon_key": f"{_STATIC_BASE}/badges/crab-heart.png{_MEDAL_IMG_V}",
        "description": "活动期间登录追潮记即可获得。",
        "requirements": [{"text": "10.7-10.17 期间登录"}], "rewards": {"score": 30}, "sort": 6,
    },
    {
        "id": "medal_7", "title": "深蓝百万里", "display_title": "深蓝百万里", "rarity": "common",
        "icon_key": f"{_STATIC_BASE}/badges/crab-map.png{_MEDAL_IMG_V}",
        "description": "点进「深蓝百万里」看一看。",
        "requirements": [{"text": "进入「深蓝百万里」"}], "rewards": {"score": 30}, "sort": 7,
    },
    {
        "id": "medal_8", "title": "……好吧也比没有强", "display_title": "……好吧也比没有强", "rarity": "common",
        "icon_key": f"{_STATIC_BASE}/badges/crab-dig.png{_MEDAL_IMG_V}",
        "description": "捡到一块石头也算收获。",
        "requirements": [{"text": "识别到一块石头"}], "rewards": {"score": 30}, "sort": 8,
    },
    {
        "id": "medal_9", "title": "蟹蟹！", "display_title": "蟹蟹！", "rarity": "common",
        "icon_key": f"{_STATIC_BASE}/badges/crab-cloud.png{_MEDAL_IMG_V}",
        "description": "第一次识别到螃蟹。",
        "requirements": [{"text": "识别到螃蟹"}], "rewards": {"score": 40}, "sort": 9,
    },
    {
        "id": "medal_10", "title": "蟹老板", "display_title": "蟹老板", "rarity": "rare",
        "icon_key": f"{_STATIC_BASE}/badges/crab-crown.png{_MEDAL_IMG_V}",
        "description": "识别到很多只螃蟹，蟹老板本蟹。",
        "requirements": [{"text": "累计识别 10 次螃蟹"}], "rewards": {"score": 80}, "sort": 10,
    },
    {
        "id": "medal_11", "title": "海景房", "display_title": "“海景房”", "rarity": "epic",
        "icon_key": f"{_STATIC_BASE}/badges/crab-star.png{_MEDAL_IMG_V}",
        "description": "一次拍照就认出三种及以上潮间带生物。",
        "requirements": [{"text": "单次识别出 3 种以上生物"}], "rewards": {"score": 120}, "sort": 11,
    },
    {
        "id": "medal_12", "title": "海星拾趣", "display_title": "海星拾趣", "rarity": "rare",
        # 原本和 medal_6 共用 crab-heart.png；正式图案是一枚一张，这里换成独立文件
        "icon_key": f"{_STATIC_BASE}/badges/medal_12.png{_MEDAL_IMG_V}",
        "description": "拍照识别到海星。",
        "requirements": [{"text": "识别到海星"}], "rewards": {"score": 60}, "sort": 12,
    },
    {
        "id": "medal_13", "title": "海洋记录员", "display_title": "这就是…海洋记录员？", "rarity": "epic",
        "icon_key": f"{_STATIC_BASE}/badges/crab-book.png{_MEDAL_IMG_V}",
        "description": "累计识别出足够多种不同的生物。",
        "requirements": [{"text": "累计识别 10 种不同生物"}], "rewards": {"score": 150}, "sort": 13,
    },
    {
        "id": "medal_14", "title": "深蓝小卫士", "display_title": "深蓝小卫士", "rarity": "rare",
        "icon_key": f"{_STATIC_BASE}/badges/crab-helmet.png{_MEDAL_IMG_V}",
        "description": "用垃圾识别认出海边的垃圾。",
        "requirements": [{"text": "拍照识别出海洋垃圾"}], "rewards": {"score": 80}, "sort": 14,
    },
    {
        "id": "medal_15", "title": "海的味道我知道", "display_title": "海的味道我知道！", "rarity": "rare",
        "icon_key": f"{_STATIC_BASE}/badges/crab-checklist.png{_MEDAL_IMG_V}",
        "description": "拍照识别到紫菜。",
        "requirements": [{"text": "识别到紫菜"}], "rewards": {"score": 60}, "sort": 15,
    },
    {
        "id": "medal_16", "title": "siuuuuuu", "display_title": "siuuuuuu～～～", "rarity": "epic",
        "icon_key": f"{_STATIC_BASE}/badges/crab-astronaut.png{_MEDAL_IMG_V}",
        "description": "把图鉴里所有以「螺」结尾的生物都集齐。",
        "requirements": [{"text": "集齐图鉴中所有「螺」类生物"}], "rewards": {"score": 150}, "sort": 16,
    },
    {
        "id": "medal_17", "title": "洞口常含盐巴", "display_title": "为什么我的洞口常含盐巴", "rarity": "rare",
        # 原本和 medal_8 共用 crab-dig.png；正式图案是一枚一张，这里换成独立文件
        "icon_key": f"{_STATIC_BASE}/badges/medal_17.png{_MEDAL_IMG_V}",
        "description": "抓到蛏子并拍照识别。",
        "requirements": [{"text": "识别到蛏子"}], "rewards": {"score": 60}, "sort": 17,
    },
    {
        "id": "medal_18", "title": "拍我干什么", "display_title": "拍我干什么？", "rarity": "rare",
        "icon_key": f"{_STATIC_BASE}/badges/crab-search.png{_MEDAL_IMG_V}",
        "description": "拍照识别到藤壶。",
        "requirements": [{"text": "识别到藤壶"}], "rewards": {"score": 60}, "sort": 18,
    },
]


async def _seed_missing(session: AsyncSession, model, items: list[dict]) -> None:
    """按主键补齐缺失的种子数据（幂等，可重复执行，不覆盖已存在的行）。"""
    ids = [item["id"] for item in items]
    existing = set(
        (await session.execute(select(model.id).where(model.id.in_(ids)))).scalars().all()
    )
    missing = [item for item in items if item["id"] not in existing]
    if missing:
        session.add_all([model(**item) for item in missing])


async def _remove_deprecated_species(session: AsyncSession) -> None:
    """清理已废弃 / 临时下线的图鉴条目及其关联数据（幂等）。"""
    targets = tuple(DEPRECATED_SPECIES) + tuple(HIDDEN_SPECIES)
    if not targets:
        return
    await session.execute(
        delete(Favorite).where(
            Favorite.target_type == "species", Favorite.target_id.in_(targets)
        )
    )
    await session.execute(
        delete(SpeciesPhoto).where(SpeciesPhoto.species_id.in_(targets))
    )
    await session.execute(delete(Species).where(Species.id.in_(targets)))


async def _remove_deprecated_spots(session: AsyncSession) -> None:
    """删除已下线点位及其调和常数（幂等）。

    坐标没实地核对前不发布，直接从库里摘掉。用户产生的卡片 / 社区笔记 / 签到
    等历史数据不动：这些地方读点位都是 `db.get(Spot, id)`，取不到会自己兜底。
    """
    if not DEPRECATED_SPOTS:
        return
    await session.execute(delete(Spot).where(Spot.id.in_(DEPRECATED_SPOTS)))
    await session.execute(
        delete(SpotHarmonics).where(SpotHarmonics.spot_id.in_(DEPRECATED_SPOTS))
    )


async def _remove_deprecated_medals(session: AsyncSession) -> None:
    """删除已废弃的勋章，**并把受影响用户的积分重算**（幂等）。

    为什么必须重算：`users.score` = 已解锁勋章 rewards.score 的累计，而
    `/achievements/overview` 的完成度就是 score / 剩余勋章总分。勋章删了但分留着的话，
    完成度会算到 100% 以上、等级（xp = score + 去过的点位 × 60）也跟着虚高。
    等级本身不用动 —— 它会在下次读 /auth/me 时按新分数自动重算。
    """
    if not DEPRECATED_MEDALS:
        return
    affected = (
        await session.execute(
            select(UserMedal.user_id).where(UserMedal.medal_id.in_(DEPRECATED_MEDALS)).distinct()
        )
    ).scalars().all()
    await session.execute(delete(UserMedal).where(UserMedal.medal_id.in_(DEPRECATED_MEDALS)))
    await session.execute(delete(Medal).where(Medal.id.in_(DEPRECATED_MEDALS)))
    for uid in affected:
        left_ids = (
            await session.execute(select(UserMedal.medal_id).where(UserMedal.user_id == uid))
        ).scalars().all()
        total = 0
        if left_ids:
            left = (
                await session.execute(select(Medal).where(Medal.id.in_(left_ids)))
            ).scalars().all()
            total = sum(int((m.rewards or {}).get("score") or 0) for m in left)
        user = await session.get(User, uid)
        if user is not None:
            user.score = total


async def _sync_team_source(session: AsyncSession) -> None:
    """把已有点位/图鉴的来源署名同步成最新团队名（幂等）。

    改名后旧行仍写着「观潮团队整理」，_seed_missing 只补新行不会改旧行，
    所以这里显式刷一遍。
    """
    for model in (Spot, Species):
        rows = (
            await session.execute(select(model).where(model.source == "观潮团队整理"))
        ).scalars().all()
        for row in rows:
            row.source = "追潮记团队整理"


async def _sync_species_covers(session: AsyncSession) -> None:
    """同步官方图鉴封面（幂等，覆盖旧值，保证新增与已存在的物种都带上图）。"""
    for species_id, cover_key in SPECIES_COVERS.items():
        species = await session.get(Species, species_id)
        if species is not None and species.cover_key != cover_key:
            species.cover_key = cover_key


SPOT_HEAT = {
    "spot_qd_01": 100,  # 会场赶海园  极热门
    "spot_qd_shilaoren": 100,  # 石老人  极热门
    "spot_qd_03": 85,  # 小麦岛  热门
    "spot_qd_04": 85,  # 王哥庄赶海园  热门
    "spot_qd_05": 70,  # 沙子口  较热门
    "spot_qd_06": 70,  # 极地海洋公园  较热门
    "spot_qd_07": 70,  # 青岛雕塑园  较热门
    "spot_qd_08": 70,  # 海之风广场  较热门
    "spot_qd_09": 40,  # 港东码头  其他（平列）
    "spot_qd_luqinghe": 40,  # 流清河沙滩  其他（平列）
    "spot_qd_11": 40,  # 南姜码头  其他（平列）
    "spot_qd_12": 40,  # 青岛铭都海岸酒店附近  其他（平列）
    "spot_qd_13": 40,  # 流清河社区附近  其他（平列）
    "spot_qd_14": 100,  # 鱼鸣嘴  极热门
    "spot_qd_15": 85,  # 连三岛  热门
    "spot_qd_16": 70,  # 银沙滩  较热门
    "spot_qd_18": 70,  # 唐岛湾南岸滩涂  较热门
    "spot_qd_19": 70,  # 灵山卫地铁站南侧滩涂  较热门
    "spot_qd_20": 70,  # 星光岛  较热门
    "spot_qd_21": 55,  # 石雀滩  一般
    "spot_qd_22": 40,  # 南屯码头  其他（平列）
    "spot_qd_23": 40,  # 虹桥待月公园  其他（平列）
    "spot_qd_26": 40,  # 海军公园  其他（平列）
    "spot_qd_27": 40,  # 贝壳广场  其他（平列）
    "spot_qd_28": 100,  # 栈桥  极热门
    "spot_qd_29": 100,  # 太平角公园  极热门
    "spot_qd_yigong": 85,  # 第一海水浴场  热门
    "spot_qd_31": 70,  # 台湾路  较热门
    "spot_qd_32": 70,  # 东海饭店  较热门
    "spot_qd_33": 40,  # 燕儿岛假日海滩  其他（平列）
    "spot_qd_34": 40,  # 奥帆中心  其他（平列）
    "spot_qd_35": 40,  # 银海大世界  其他（平列）
    "spot_qd_36": 85,  # 滨海公园  热门
    "spot_qd_37": 70,  # 盘龙庄村附近  较热门
    "spot_qd_38": 40,  # 冯家河村  其他（平列）
    "spot_qd_40": 40,  # 于家沟  其他（平列）
    "spot_qd_41": 40,  # 羊羔涧  其他（平列）
    "spot_wh_chengshantou": 75,  # 威海·成山头
    "spot_xm_01": 85,  # 厦门·白城沙滩
    "spot_xm_02": 85,  # 厦门·黄厝海滩
    "spot_xm_03": 70,  # 厦门·观音山沙滩
    "spot_xm_04": 70,  # 厦门·五缘湾湿地公园
    "spot_xm_05": 55,  # 厦门·杏林湾
    "spot_xm_06": 70,  # 厦门·大嶝岛
    "spot_xm_07": 55,  # 厦门·小嶝岛
    "spot_xm_08": 55,  # 厦门·海沧湾公园
    "spot_xm_09": 85,  # 厦门·鼓浪屿港仔后沙滩
    "spot_hn_hk_01": 85,  # 海口·假日海滩
    "spot_hn_hk_02": 70,  # 海口·西秀海滩
    "spot_hn_hk_03": 70,  # 海口·白沙门沙滩
    "spot_hn_hk_04": 55,  # 海口·东寨港红树林
    "spot_hn_wc_01": 70,  # 文昌·清澜港
    "spot_hn_wc_02": 85,  # 文昌·铜鼓岭石头公园
    "spot_hn_qh_01": 85,  # 琼海·博鳌玉带滩
    "spot_hn_wn_01": 85,  # 万宁·日月湾
    "spot_hn_wn_02": 70,  # 万宁·石梅湾
    "spot_hn_ls_01": 70,  # 陵水·清水湾
    "spot_hn_ls_02": 70,  # 陵水·黎安哪吒亲子户外俱乐部
    "spot_hn_ls_03": 70,  # 陵水·海龟沙滩
    "spot_hn_ls_04": 55,  # 陵水·岭仔村
    "spot_hn_ls_05": 55,  # 陵水·赤岭
    "spot_hn_ls_06": 55,  # 陵水·桐海村
    "spot_hn_ls_07": 70,  # 陵水·新村港东侧滩涂
    "spot_hn_ls_08": 55,  # 陵水·黎安港西北角
    "spot_hn_sy_01": 100,  # 三亚·三亚湾
    "spot_hn_sy_02": 100,  # 三亚·大东海
    "spot_hn_sy_03": 85,  # 三亚·亚龙湾
    "spot_hn_dz_01": 55,  # 儋州·白马井
    "spot_hn_df_01": 70,  # 东方·鱼鳞洲
    "spot_hn_cj_01": 70,  # 昌江·棋子湾
    "spot_hn_cm_01": 55,  # 澄迈·盈滨半岛
}


async def _sync_spot_fields(session: AsyncSession) -> None:
    """同步已有点位的热度、所属区与坐标（幂等，覆盖旧值）。

    这些字段会随运营调整（热度排名更新、行政区划与坐标补全），而
    _seed_missing 只补缺失行、不覆盖已存在的行 —— 老点位就吃不到新值。
    所以这里单独同步一次。
    """
    for item in SPOTS:
        spot = await session.get(Spot, item["id"])
        if spot is None:
            continue
        if item["id"] in SPOT_HEAT:
            spot.heat = SPOT_HEAT[item["id"]]
        district = item.get("district")
        if district:
            spot.district = district
        # 坐标只在种子里有值时才覆盖 —— 留空的点位（如顾家岛码头，
        # 高德没搜到准确位置）不能把已有坐标抹掉
        if item.get("lat") is not None and item.get("lng") is not None:
            spot.lat = item["lat"]
            spot.lng = item["lng"]


async def _sync_medal_fields(session: AsyncSession) -> None:
    """同步已入库勋章的分值与稀有度（幂等，覆盖旧值）。

    分值和稀有度是「数值平衡」，会随着设计调整；而 _seed_missing 只插不改，
    光改这个文件线上已有的勋章不会变 —— 所以单独同步一次。

    ⚠️ 刻意**不同步 icon_key**：众筹成就的图现在还是占位图，正式图案很可能是
    直接换库/换文件，同步会把人工替换的结果覆盖回去。
    """
    for item in MEDALS:
        medal = await session.get(Medal, item["id"])
        if medal is None:
            continue
        medal.rarity = item["rarity"]
        medal.rewards = item["rewards"]


async def seed_if_empty(session: AsyncSession) -> None:
    """按主键补齐种子数据（幂等）。表里已有的行不会被覆盖。"""
    await _seed_missing(session, Spot, SPOTS)
    await _remove_deprecated_spots(session)
    await _sync_spot_fields(session)
    await _seed_missing(session, Species, SPECIES)
    await _remove_deprecated_species(session)
    await _sync_species_covers(session)
    await _sync_team_source(session)
    await _seed_missing(session, Quiz, QUIZZES)
    await _seed_missing(session, QuizQuestion, QUIZ_QUESTIONS)
    await _seed_missing(session, Medal, MEDALS)
    await _sync_medal_fields(session)
    await _remove_deprecated_medals(session)
    await _seed_missing(session, CommunityTopic, COMMUNITY_TOPICS)
    await _seed_missing(session, Knowledge, KNOWLEDGE)
    await _seed_missing(session, LightMap, LIGHT_MAPS)
    await _seed_missing(session, Activity, ACTIVITIES)
    await session.commit()
