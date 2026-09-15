@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==============================================
echo            观潮后端 - 启动中
echo ==============================================
echo.

rem 检查虚拟环境是否就绪
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv，请先安装依赖：
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo [1/2] 启动 FastAPI 服务 http://0.0.0.0:8000 ...
echo [2/2] 接口文档: http://127.0.0.1:8000/docs
echo.
echo 关闭本窗口即可停止服务。
echo.

.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo 服务已停止。
pause
