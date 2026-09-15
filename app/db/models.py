from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    openid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64), default="追潮探索者")
    avatar_key: Mapped[str] = mapped_column(String(255), default="")
    level: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(64), default="海洋探索家")
    score: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    need_guardian_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    guardian_consented: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Spot(Base):
    __tablename__ = "spots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    city: Mapped[str] = mapped_column(String(32))
    cover_key: Mapped[str] = mapped_column(String(255), default="")
    open_time: Mapped[str] = mapped_column(String(128), default="")
    age_hint: Mapped[str] = mapped_column(String(128), default="")
    safety_tags: Mapped[list] = mapped_column(JSON, default=list)
    observe_hint: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    gear_list: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(255), default="")
    reviewed_at: Mapped[str] = mapped_column(String(32), default="")
    lat: Mapped[float] = mapped_column(Float, nullable=True)
    lng: Mapped[float] = mapped_column(Float, nullable=True)
    heat: Mapped[int] = mapped_column(Integer, default=0)


class Species(Base):
    __tablename__ = "species"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    aka: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(16), index=True)  # shell/crab/algae/fish/other
    cover_key: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    look: Mapped[str] = mapped_column(Text, default="")
    habitat: Mapped[str] = mapped_column(String(128), default="")
    observe_tip: Mapped[str] = mapped_column(Text, default="")
    safety_tip: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(255), default="")
    reviewed_at: Mapped[str] = mapped_column(String(32), default="")
    protected: Mapped[bool] = mapped_column(Boolean, default=False)


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    cover_key: Mapped[str] = mapped_column(String(255), default="")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    quiz_id: Mapped[str] = mapped_column(String(64), ForeignKey("quizzes.id"), index=True)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)  # [{"id":"A","text":"..."}]
    correct_option_id: Mapped[str] = mapped_column(String(8))
    explain: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Medal(Base):
    __tablename__ = "medals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    display_title: Mapped[str] = mapped_column(String(64))
    rarity: Mapped[str] = mapped_column(String(16))  # common/rare/epic/legendary/hidden
    icon_key: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[list] = mapped_column(JSON, default=list)  # [{"text":"..."}]
    rewards: Mapped[dict] = mapped_column(JSON, default=dict)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(16))  # species | card
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, default="")  # user:{id} 或 client:{id}
    scene: Mapped[str] = mapped_column(String(16), default="observation")  # speciesGuess|observation|card
    content_type: Mapped[str] = mapped_column(String(64), default="")
    ext: Mapped[str] = mapped_column(String(8), default="jpg")
    object_key: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing|approved|rejected
    width: Mapped[int] = mapped_column(Integer, nullable=True)
    height: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SpeciesPhoto(Base):
    """用户上传到图鉴物种下的照片（仅上传者可见，不进公共图鉴）。"""

    __tablename__ = "species_photos"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    species_id: Mapped[str] = mapped_column(String(64), index=True)
    upload_id: Mapped[str] = mapped_column(String(64), default="")
    object_key: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    upload_id: Mapped[str] = mapped_column(String(64), default="")
    spot_id: Mapped[str] = mapped_column(String(64), default="")
    observed_at: Mapped[str] = mapped_column(String(32), default="")
    guess_id: Mapped[str] = mapped_column(String(64), default="")
    user_note: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(64), default="今日观潮")
    observations: Mapped[list] = mapped_column(JSON, default=list)
    safety_tip: Mapped[str] = mapped_column(Text, default="")
    tide_line: Mapped[str] = mapped_column(String(128), default="")
    related_species: Mapped[list] = mapped_column(JSON, default=list)
    cover_key: Mapped[str] = mapped_column(String(255), default="")
    share_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Guess(Base):
    __tablename__ = "guesses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True, default="")  # user:{id} 或 client:{id}
    upload_id: Mapped[str] = mapped_column(String(64), default="")
    spot_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="done")
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    # 当照片里不是海洋生物时，AI 认为它到底是什么（如「鼠标」「键盘」）
    object_name: Mapped[str] = mapped_column(String(64), default="")
    observe_tip: Mapped[str] = mapped_column(Text, default="")
    safety_tip: Mapped[str] = mapped_column(Text, default="")
    disclaimer: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Checkin(Base):
    __tablename__ = "checkins"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    spot_id: Mapped[str] = mapped_column(String(64), default="")
    checkin_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # attemptId
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    quiz_id: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    wrong: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserMedal(Base):
    __tablename__ = "user_medals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    medal_id: Mapped[str] = mapped_column(String(64), index=True)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acked: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="other")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TideCache(Base):
    """每日潮汐缓存：按点位+日期保存当日拉取到的潮汐曲线。

    - date 为北京时间日期 YYYY-MM-DD；
    - data 存 {"hourly": [{time, heightM}], "points": [{time, heightM, type}]}，
      即当日整天的逐时曲线与高低潮点，不存与当前时刻绑定的 currentHeightM/trend。
    """

    __tablename__ = "tide_cache"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    spot_id: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommunityTopic(Base):
    """社区话题（观察分享的分类标签）。"""

    __tablename__ = "community_topics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class CommunityNote(Base):
    """社区笔记（观察分享）。

    - topic_ids 为话题 id 列表（JSON）；
    - image_upload_ids 为上传图 id 列表（JSON），封面取第一张；
    - visibility：public | reviewing | private，reviewing 仅作者可见。
    """

    __tablename__ = "community_notes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, default="")
    topic_ids: Mapped[list] = mapped_column(JSON, default=list)
    spot_id: Mapped[str] = mapped_column(String(64), default="")
    species_id: Mapped[str] = mapped_column(String(64), default="")
    image_upload_ids: Mapped[list] = mapped_column(JSON, default=list)
    cover_height: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[str] = mapped_column(String(16), default="public")
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    favorite_count: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommunityNoteLike(Base):
    __tablename__ = "community_note_likes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    note_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommunityNoteFavorite(Base):
    __tablename__ = "community_note_favorites"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    note_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommunityComment(Base):
    """社区评论。reply_to_id / reply_to_nickname 冗余存被回复评论的定位信息。"""

    __tablename__ = "community_comments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(64), ForeignKey("community_notes.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    reply_to_id: Mapped[str] = mapped_column(String(64), default="")
    reply_to_nickname: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommunityFollow(Base):
    """关注关系：follower_id 关注 followee_id。"""

    __tablename__ = "community_follows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    follower_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    followee_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExploreSession(Base):
    """场地探索会话。grid_ids 存网格编号列表，idempotency_key 保证幂等。"""

    __tablename__ = "explore_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    venue_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="solo")  # solo | group
    grid_ids: Mapped[list] = mapped_column(JSON, default=list)
    explore_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    badge_title: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[str] = mapped_column(String(32), default="")
    ended_at: Mapped[str] = mapped_column(String(32), default="")
    idempotency_key: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Reminder(Base):
    """潮汐提醒：用户在日历里选某天某时间，提前 1 小时发订阅消息提醒。

    - remind_at 存北京时间（naive datetime，形如 2026-08-31 14:30）；
    - status：pending（待发送）| sent（已发送）| cancelled（已取消）。
    """

    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    spot_id: Mapped[str] = mapped_column(String(64), default="")
    remind_at: Mapped[datetime] = mapped_column(DateTime)  # 北京时间 naive
    note: Mapped[str] = mapped_column(String(128), default="赶海提醒")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Knowledge(Base):
    """科普文章（知识页）。"""

    __tablename__ = "knowledge"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class WatchRecord(Base):
    """观潮观察记录。owner_id 为 user:{id}（登录）或 client:{id}（游客）。

    - ended_at 为空表示进行中的会话；非空表示已结束的记录。
    - species 存 [{"name": "...", "time": "HH:MM"}]。
    """

    __tablename__ = "watch_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    spot_id: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[str] = mapped_column(String(32), default="")
    ended_at: Mapped[str] = mapped_column(String(32), default="")
    species: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LightMap(Base):
    """点亮地图（静态目录）。regions 存 [{id,name,hint,minLat,minLng,maxLat,maxLng,left,top,width,height}]。"""

    __tablename__ = "light_maps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    map_url: Mapped[str] = mapped_column(String(255), default="")
    regions: Mapped[list] = mapped_column(JSON, default=list)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Activity(Base):
    """首页活动轮播横幅。"""

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tag: Mapped[str] = mapped_column(String(16), default="")
    title: Mapped[str] = mapped_column(String(64), default="")
    desc: Mapped[str] = mapped_column(String(128), default="")
    theme: Mapped[str] = mapped_column(String(16), default="teal")  # teal | yellow | blue
    image: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(255), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    # card：左侧文字 + 右侧小插图；banner：整幅宽图铺满卡片
    layout: Mapped[str] = mapped_column(String(16), default="card")  # card | banner


class CheckinSession(Base):
    """签到活动（组织者地图选点建围栏，生成密钥给参与者）。

    - key 为 6 位密钥，参与者凭它加入；status: active | closed
    - 围栏：以 (lat, lng) 为圆心、radius_m 为半径
    """

    __tablename__ = "checkin_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)  # user:{id} 或 client:{id}
    key: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="研学报到")
    place_name: Mapped[str] = mapped_column(String(64), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=500)
    # 高德 AOI 等来源的真实边界 [[lng, lat], ...]；为空时按「圆心 + 半径」圆形围栏判定
    polygon: Mapped[list] = mapped_column(JSON, default=list)
    poi_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReportCheckin(Base):
    """研学报到签到（姓名 + 学号 + 粗粒度位置）。

    - 位置按约 300 米网格取整后存，不落精确经纬度；
    - session_id 非空表示这次报到属于某个「签到活动」（填密钥进来的）。
    """

    __tablename__ = "report_checkins"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)  # user:{id} 或 client:{id}
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    name: Mapped[str] = mapped_column(String(32))
    student_no: Mapped[str] = mapped_column(String(32))
    lat_grid: Mapped[float] = mapped_column(Float, nullable=True)
    lng_grid: Mapped[float] = mapped_column(Float, nullable=True)
    checkin_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LightMapLit(Base):
    """点亮地图的已点亮区块。owner_id 同 WatchRecord。"""

    __tablename__ = "light_map_lits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    map_id: Mapped[str] = mapped_column(String(64), index=True)
    region_id: Mapped[str] = mapped_column(String(64))
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
