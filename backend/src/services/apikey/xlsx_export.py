# -*- coding: utf-8 -*-
"""用标准库生成简单、可筛选的调用明细 XLSX，避免运行时依赖桌面 Excel。"""
from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _inline_cell(ref: str, value, style: int = 0) -> str:
    text = escape("" if value is None else str(value))
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t>{text}</t></is></c>'


def _number_cell(ref: str, value, style: int = 0) -> str:
    return f'<c r="{ref}" s="{style}"><v>{value or 0}</v></c>'


def usage_details_xlsx(rows: list[dict], *, currency: str, rmb_per_usd: float) -> bytes:
    code = currency.upper()
    symbol = "¥" if code == "RMB" else "$"
    headers = ["时间", "用户邮箱", "请求 ID", "模型", "Slot", "输入 Token", "输出 Token",
               "缓存读 Token", "缓存写 Token", "总 Token", f"花费 ({code})", "状态", "错误码"]
    widths = [21, 28, 28, 24, 22, 14, 14, 16, 16, 14, 16, 12, 18]
    sheet_rows = []
    header_cells = "".join(_inline_cell(f"{_col_name(i)}1", value, 1) for i, value in enumerate(headers, 1))
    sheet_rows.append(f'<row r="1" ht="24">{header_cells}</row>')
    for row_index, row in enumerate(rows, 2):
        created = row.get("created_at")
        if hasattr(created, "strftime"):
            if getattr(created, "tzinfo", None) is not None:
                created = created.astimezone(ZoneInfo("Asia/Shanghai"))
            created = created.strftime("%Y-%m-%d %H:%M:%S")
        cache_read = int(row.get("cache_read_tokens") or 0)
        cache_write = int(row.get("cache_write_tokens") or 0)
        cost = float(row.get("cost_micro_usd") or 0) / 1_000_000
        if code == "RMB":
            cost *= float(rmb_per_usd)
        values = [created, row.get("email"), row.get("request_id"), row.get("model"), row.get("slot_id")]
        numbers = [row.get("prompt_tokens"), row.get("completion_tokens"), cache_read, cache_write,
                   row.get("total_tokens")]
        cells = [_inline_cell(f"{_col_name(i)}{row_index}", value) for i, value in enumerate(values, 1)]
        cells += [_number_cell(f"{_col_name(i)}{row_index}", value, 2) for i, value in enumerate(numbers, 6)]
        cells.append(_number_cell(f"K{row_index}", f"{cost:.6f}", 3))
        cells.append(_inline_cell(f"L{row_index}", row.get("status")))
        cells.append(_inline_cell(f"M{row_index}", row.get("error_code")))
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    last_row = max(1, len(rows) + 1)
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
                   for i, width in enumerate(widths, 1))
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>{cols}</cols><sheetData>{''.join(sheet_rows)}</sheetData>
<autoFilter ref="A1:M{last_row}"/></worksheet>'''
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="{symbol}#,##0.000000"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="调用明细" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()
