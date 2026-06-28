# -*- coding: utf-8 -*-
"""注册/登录图形验证码：生成 4 位字符 SVG，答案存 Redis 单次消费。

用 SVG 自绘（无 Pillow 等二进制依赖）：
- issue()  → {captcha_id, image(data:image/svg+xml;base64,...)}，答案写 Redis（TTL 5 分钟，小写）
- verify(captcha_id, text, consume=True) → bool；consume 时无论对错都删 key（防重放/暴力）
"""
from __future__ import annotations

import base64
import logging
import secrets
import uuid

from utils import redis as redis_util

log = logging.getLogger("ak.captcha")

# 去掉易混字符（0/O、1/I/L）
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_LEN = 4
_TTL = 300  # 5 分钟
_W, _H = 130, 44
_KEY = "ak:captcha:{}"


def _rint(a: int, b: int) -> int:
    return a + secrets.randbelow(b - a + 1)


def _render_svg(code: str) -> str:
    """手写 SVG：背景 + 干扰线 + 逐字符抖动旋转上色。"""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}">',
        f'<rect width="{_W}" height="{_H}" fill="#f5f6fa"/>',
    ]
    # 干扰线
    for _ in range(5):
        x1, y1, x2, y2 = _rint(0, _W), _rint(0, _H), _rint(0, _W), _rint(0, _H)
        g = _rint(160, 210)
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="rgb({g},{g},{g})" stroke-width="1"/>'
        )
    # 干扰点
    for _ in range(40):
        cx, cy, g = _rint(0, _W), _rint(0, _H), _rint(150, 220)
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="1" fill="rgb({g},{g},{g})"/>')
    # 逐字符：抖动 + 旋转 + 深色
    x = 14
    for ch in code:
        y = _rint(28, 36)
        rot = _rint(-25, 25)
        r, gg, b = _rint(20, 90), _rint(20, 90), _rint(90, 160)
        parts.append(
            f'<text x="{x}" y="{y}" font-family="monospace" font-size="30" '
            f'font-weight="bold" fill="rgb({r},{gg},{b})" '
            f'transform="rotate({rot} {x} {y})">{ch}</text>'
        )
        x += 28
    parts.append("</svg>")
    return "".join(parts)


async def issue() -> dict:
    code = "".join(_ALPHABET[secrets.randbelow(len(_ALPHABET))] for _ in range(_LEN))
    cid = uuid.uuid4().hex
    await redis_util.setex(_KEY.format(cid), _TTL, code.lower())
    b64 = base64.b64encode(_render_svg(code).encode("utf-8")).decode("ascii")
    return {"captcha_id": cid, "image": f"data:image/svg+xml;base64,{b64}"}


async def verify(captcha_id: str | None, text: str | None, *, consume: bool = True) -> bool:
    """校验。consume=True 单次消费用完即删；consume=False 只校验不删（发邮箱码这种中间步骤用）。"""
    if not captcha_id or not text:
        return False
    key = _KEY.format(captcha_id)
    saved = await redis_util.get(key)
    if consume:
        try:
            await redis_util.delete(key)
        except Exception:
            pass
    if saved is None:
        return False
    if isinstance(saved, bytes):
        saved = saved.decode("utf-8", "ignore")
    return saved.strip().lower() == text.strip().lower()
