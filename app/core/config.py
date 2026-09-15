from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 数据库
    database_url: str = "mysql+asyncmy://root:root@127.0.0.1:3306/guanchao"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # JWT
    jwt_secret: str = "dev-secret-change-me"
    access_token_expire_seconds: int = 7200
    refresh_token_expire_seconds: int = 2592000  # 30 天

    # 微信小程序（空则走 mock 登录）
    wechat_appid: str = ""
    wechat_secret: str = ""

    # 通义千问（DashScope，OpenAI 兼容模式）
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_text_model: str = "qwen-plus"
    qwen_vl_model: str = "qwen-vl-max"

    # 潮汐数据（和风天气 QWeather 海洋潮汐 API）
    tide_api_host: str = "https://nq3jpjg4yq.re.qweatherapi.com"
    tide_location_id: str = "P2717"
    tide_api_key: str = ""

    # 高德地图 Web 服务（地点搜索 + AOI 边界）。留空则签到用圆形围栏，自动降级。
    amap_key: str = ""

    # AI 生图（通义万相 wanx，DashScope 原生异步接口）
    image_gen_model: str = "wanx2.1-t2i-plus"
    image_edit_model: str = "wanx2.1-imageedit"


settings = Settings()
