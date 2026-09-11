"""客户端下载: 登录后可下载打包好的客户端 exe。

网页管理端的"exe 客户端接入信息"卡片提供下载按钮;
exe 位于仓库 client/dist/ (由 client/build.bat 或 PyInstaller 构建)。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..config import BASE_DIR
from ..deps import get_current_user

router = APIRouter(prefix="/api/client", tags=["client"])

CLIENT_EXE = BASE_DIR.parent / "client" / "dist" / "nattunnel-client.exe"


@router.get("/download")
def download_client(user=Depends(get_current_user)):
    """流式返回 client/dist/nattunnel-client.exe(需有效 Bearer 令牌)。"""
    if not CLIENT_EXE.is_file():
        raise HTTPException(status_code=404, detail="客户端 exe 未构建: 请先在 client/ 目录运行 build.bat")
    return FileResponse(
        path=str(CLIENT_EXE),
        media_type="application/octet-stream",
        filename="nattunnel-client.exe",
    )
