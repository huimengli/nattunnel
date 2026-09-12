"""客户端下载: 登录后可下载打包好的客户端 exe。

网页管理端的"exe 客户端接入信息"卡片提供两条下载通道, **读同一份文件**:
1) 本接口(需有效 Bearer 令牌);
2) 静态资产直链 /nattunnel-client.exe(免登录, StaticFiles 挂载直接提供)。
两者均指向 server/app/static/nattunnel-client.exe — build.bat 构建后自动把
dist 产物复制到该处, 服务器端只需维护这一份副本。
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..config import BASE_DIR
from ..deps import get_current_user

router = APIRouter(prefix="/api/client", tags=["client"])

CLIENT_EXE = BASE_DIR / "app" / "static" / "nattunnel-client.exe"


@router.get("/download")
def download_client(user=Depends(get_current_user)):
    """流式返回 app/static/nattunnel-client.exe(需有效 Bearer 令牌)。"""
    if not CLIENT_EXE.is_file():
        raise HTTPException(status_code=404, detail="客户端 exe 未部署: 请在 client/ 运行 build.bat(自动同步到 server/app/static/)或手工上传该文件")
    return FileResponse(
        path=str(CLIENT_EXE),
        media_type="application/octet-stream",
        filename="nattunnel-client.exe",
    )
