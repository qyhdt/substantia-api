# -*- coding: utf-8 -*-


def preview_text(text: str, limit: int = 200) -> str:
    """用于日志的安全预览文本：去换行 + 截断。"""
    if not text:
        return ""
    text = text.replace("\n", " ")
    return text[:limit] + "..." if len(text) > limit else text
