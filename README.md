# 观潮小程序后端

微信小程序「观潮」后端（FastAPI）。本期交付：核心骨架 + 微信登录鉴权 + 全部读接口。

## 环境要求

- Python 3.12（已装）
- MySQL 8.4（已装，服务名 `MySQL84`，端口 3306）
- Redis 5.0.14（已装，`E:\dev-tools\Redis\redis-server.exe`，本期可选）

## 快速开始

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 准备配置
copy .env.example .env
#    编辑 .env：填入 DATABASE_URL 里的 MySQL root 密码

# 3. 建库（一次性）
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS guanchao CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"

# 4. 启动（首次启动自动建表 + 灌入种子数据）
uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000/docs 查看 Swagger 文档并联调。

## 快速联调

```bash
# 读接口（游客）
curl http://127.0.0.1:8000/api/v1/spots
curl http://127.0.0.1:8000/api/v1/encyclopedia
curl "http://127.0.0.1:8000/api/v1/home/today?spotId=spot_qd_shilaoren"

# 登录（无微信密钥时走 mock 模式）
curl -X POST http://127.0.0.1:8000/api/v1/auth/wechat-login -H "Content-Type: application/json" -d "{\"code\":\"test\"}"

# 带 token 访问
curl http://127.0.0.1:8000/api/v1/me -H "Authorization: Bearer <accessToken>"
```

## 管理后台（审核宝藏点位）

用户上传的宝藏点位默认是私有的。管理员通过后台审核后，投稿会发布成官方点位（写进
`spots` 表），前台 `GET /spots` 立刻可见。

1. 在 `.env` 里配置一个足够长的随机串：`ADMIN_TOKEN=<你的令牌>`（**不填则 `/admin` 相关接口全部 401**）。
2. 重启服务，浏览器打开 http://127.0.0.1:8000/admin ，在页面里填入同一个令牌。
3. 待审核列表里可通过（可先编辑名称/城市/简介再发布）或驳回。

接口细节见 `docs/API.md` §一.3。

## 目录结构

```
app/
  main.py            # 入口
  core/              # 配置、统一响应、错误码、JWT、依赖
  db/                # 引擎、模型、种子数据
  schemas/           # Pydantic 模型
  services/          # 微信 / 潮汐 / AI 服务
  api/v1/endpoints/  # 各业务端点
  static/admin.html  # 审核后台页面（GET /admin）
```

## 统一响应

所有接口返回 `{code, message, data, requestId}`；错误码见接口文档 §1.5。
