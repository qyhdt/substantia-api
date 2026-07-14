# -*- coding: utf-8 -*-
"""
上传：目前仅用于「转账凭证」图片。存到本地盘（settings.UPLOAD_DIR，容器挂载卷），
通过 /api/uploads/<file> 回读。对应 Go internal/handlers/uploads.go。

- 上传端点在门户路由（controller/portal.py 的 POST /portal/uploads/proof，已带登录鉴权），
  复用本文件的 save_proof()。
- 本文件只保留回读端点 GET /uploads/{name}，同样需登录（凭证含支付隐私，随机文件名 + 需登录，
  避免公网枚举）。
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config.settings import settings
from security.dependencies import require_access_token

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(require_access_token)])

UPLOAD_MAX_BYTES = 5 << 20  # 5 MB

# content-type → 扩展名白名单。
_ALLOWED_IMAGE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_EXT_TO_CT = {ext: ct for ct, ext in _ALLOWED_IMAGE_EXT.items()}


def _sniff_content_type(head: bytes) -> str:
    """按魔数嗅探图片真实类型（只认白名单里的四种）。对应 Go http.DetectContentType。"""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return ""


async def save_proof(file: UploadFile) -> str:
    """校验并落盘一张凭证图片，返回可回读的相对 URL（/api/uploads/<name>）。
    上传端点在门户路由里调用（已鉴权）。校验：≤5MB、图片魔数、随机 hex 文件名。"""
    data = await file.read(UPLOAD_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="missing file")
    if len(data) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 5MB)")

    # 嗅探真实 content-type（前 512 字节），只允许图片。
    ext = _ALLOWED_IMAGE_EXT.get(_sniff_content_type(data[:512]))
    if not ext:
        raise HTTPException(status_code=415, detail="only png/jpeg/webp/gif images allowed")

    dir_ = settings.UPLOAD_DIR
    try:
        os.makedirs(dir_, exist_ok=True)
    except OSError:
        raise HTTPException(status_code=500, detail="upload dir not writable")

    name = secrets.token_hex(16) + ext
    try:
        with open(os.path.join(dir_, name), "wb") as f:
            f.write(data)
    except OSError:
        raise HTTPException(status_code=500, detail="cannot save file")

    return "/api/uploads/" + name


@router.get("/{name}", summary="回读一个已上传文件（需登录）")
async def serve_upload(name: str):
    # 文件名必须是本目录下的单层文件名，防目录穿越。
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="bad name")
    # 只允许我们生成的形态：已知扩展名。
    ext = os.path.splitext(name)[1].lower()
    if ext not in _EXT_TO_CT:
        raise HTTPException(status_code=400, detail="bad name")
    path = os.path.join(settings.UPLOAD_DIR, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type=_EXT_TO_CT[ext])
