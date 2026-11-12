# 潮池 AI 抠图模型

潮池的 AI 抠图用 rembg 的 **u2netp** 模型（CPU 可跑，约 4.4MB）。

## 文件位置

```
guanchao-backend/
  models/
    u2netp/
      u2netp.onnx
```

`app/services/cutout.py` 会把 `U2NET_HOME` 指到后端根目录，rembg 会在这里找
`models/u2netp/u2netp.onnx`。**不要只改文件名或目录结构**，否则会重新触发下载（或被判为未部署）。

## 下载

```bash
mkdir -p models/u2netp
curl -L -o models/u2netp/u2netp.onnx \
  https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx
```

> 注意：Python 的 requests 如果报 `CERTIFICATE_VERIFY_FAILED`（代理/证书环境常见），
> 用 curl 下载再放进去即可。服务运行时只读本地文件，不会再联网下载。

## 部署到服务器

```
/srv/guanchao/models/u2netp/u2netp.onnx
```

并确保 `rembg[cpu]` 已安装（见 `requirements.txt`），以及
`/srv/guanchao/data/numba_cache` 对服务用户 `www-data` 可写。

## 性能

- 首次加载约 2~3 秒，之后 session 常驻内存
- 1280px 输入、CPU 单张约 2 秒
- 输出是裁到 alpha 外接框、带黑/白贴纸边的透明 PNG，最长边 1000px
