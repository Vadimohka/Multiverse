import io
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font


def export_xlsx(records: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Данные"
    columns = sorted({key for row in records for key in row})
    ws.append(columns)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{chr(64+min(len(columns),26))}{max(len(records)+1,1)}" if columns else "A1:A1"
    for row in records:
        ws.append([json.dumps(row.get(c), ensure_ascii=False) if isinstance(row.get(c), (dict, list)) else row.get(c) for c in columns])
    for idx, col in enumerate(columns, 1):
        max_len = max([len(str(col))] + [len(str(row.get(col, ""))) for row in records])
        ws.column_dimensions[ws.cell(1, idx).column_letter].width = min(max_len + 2, 60)
    if metadata:
        meta = wb.create_sheet("Метаданные")
        for key, value in metadata.items():
            meta.append([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
    buffer = io.BytesIO(); wb.save(buffer)
    return buffer.getvalue()
