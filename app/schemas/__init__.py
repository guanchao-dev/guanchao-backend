from pydantic import BaseModel


class WechatLoginRequest(BaseModel):
    code: str
    clientId: str | None = None


class RefreshRequest(BaseModel):
    refreshToken: str


class TideAdviceRequest(BaseModel):
    spotId: str
    date: str | None = None


class UploadCredentialRequest(BaseModel):
    scene: str
    contentType: str
    ext: str


class UploadCompleteRequest(BaseModel):
    width: int | None = None
    height: int | None = None


class SpeciesGuessRequest(BaseModel):
    uploadId: str
    spotId: str | None = None
    tideHeightM: float | None = None
    tideTrend: str | None = None
    clientTime: str | None = None
    # 用户定位（可选）：只用于过滤掉当地不可能出现的物种，不会原样传给 AI
    lat: float | None = None
    lng: float | None = None


class TrashGuessRequest(BaseModel):
    """垃圾识别：先上传拿 uploadId，再调识别。"""

    uploadId: str
    spotId: str | None = None
    clientTime: str | None = None


class GuessFeedbackRequest(BaseModel):
    pickedSpeciesId: str | None = None
    helpful: bool = True


class CreateCardRequest(BaseModel):
    uploadId: str
    spotId: str | None = None
    observedAt: str | None = None
    guessId: str | None = None
    userNote: str | None = None


class ShareRequest(BaseModel):
    channel: str = "wechatFriend"


class QuizAnswerItem(BaseModel):
    questionId: str
    optionId: str


class QuizSubmitRequest(BaseModel):
    attemptId: str
    answers: list[QuizAnswerItem]


class GuardianConsentRequest(BaseModel):
    agreed: bool = True
    version: str | None = None


class FeedbackRequest(BaseModel):
    content: str
    contact: str | None = None


class ReportCheckinRequest(BaseModel):
    """研学报到签到。lat/lng 前端已按约 300 米网格取整。

    sessionKey 非空时表示通过「签到活动」报到，服务端会校验是否在围栏内。
    """

    name: str
    studentNo: str
    lat: float | None = None
    lng: float | None = None
    checkinAt: str | None = None
    sessionKey: str | None = None


class CheckinSessionCreateRequest(BaseModel):
    """创建签到活动（组织者地图选点）。"""

    name: str | None = None
    placeName: str
    address: str | None = None
    lat: float
    lng: float
    radiusM: int | None = 500
    # 可选：高德 AOI 等来源的真实边界 [[lng, lat], ...]；传了就用多边形围栏
    polygon: list | None = None
    poiId: str | None = None


class UnlockAckRequest(BaseModel):
    source: str | None = None
    clientTime: str | None = None


class UpdateNicknameRequest(BaseModel):
    nickname: str


class ReminderCreateRequest(BaseModel):
    spotId: str | None = None
    remindAt: str
    note: str | None = None


class WatchStartRequest(BaseModel):
    startedAt: str
    spotId: str | None = None


class WatchSpeciesRequest(BaseModel):
    name: str
    time: str | None = None
    speciesId: str | None = None
    guessId: str | None = None
    # 区分「生物」与「垃圾」，并携带垃圾的类别与位置描述
    kind: str | None = None  # species | trash
    category: str | None = None
    categoryLabel: str | None = None
    label: str | None = None
    count: int | None = None  # 同一物种 / 同类垃圾在照片里的个数


class WatchEndRequest(BaseModel):
    endedAt: str
    species: list[dict] | None = None


class LightMapVisitRequest(BaseModel):
    latitude: float
    longitude: float
    clientTime: str | None = None


class CommunityNoteCreateRequest(BaseModel):
    uploadIds: list[str]
    title: str
    content: str
    topicIds: list[str] | None = None
    spotId: str | None = None
    speciesId: str | None = None
    visibility: str | None = "public"


class CommunityCommentCreateRequest(BaseModel):
    content: str
    replyToId: str | None = None


class ExploreSessionCreateRequest(BaseModel):
    venueId: str
    mode: str
    gridIds: list[str] | None = None
    startedAt: str | None = None
    endedAt: str | None = None
    exploreRatio: float | None = None


class ExploreQrUnlockRequest(BaseModel):
    code: str
