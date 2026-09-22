"""
Lector schema-agnóstico de documentos de METADATOS (CSV / XLSX) para el
actualizador de metadatos (`metadata_ingest`). Devuelve encabezados + filas como
dicts, SIN interpretar (la clasificación/conciliación vive en el servicio).

- CSV: `csv.DictReader` (utf-8, fallback latin-1 por los exports con acentos).
- XLSX: openpyxl (import perezoso; solo donde haya xlsx). Detecta la fila de
  cabecera y lista las hojas.

Muchos catálogos (InfoVariables) traen la jerarquía "apilada" (la celda de
Dimensión/Criterio/Subcriterio solo aparece en la 1ª fila del grupo y luego va
vacía = *fill-down*). Eso NO se resuelve aquí: `forward_fill` es un helper que el
servicio aplica a las columnas que decida.
"""
from __future__ import annotations
import csv
import os


class DocTable:
    """Tabla leída de un doc: encabezados + filas (list[dict])."""

    def __init__(self, headers: list[str], rows: list[dict], sheet: str | None = None):
        self.headers = headers
        self.rows = rows
        self.sheet = sheet


def _dedup_headers(raw: list[str]) -> list[str]:
    """Encabezados ÚNICOS preservando POSICIÓN (`Definición`, `Definición_1`, …).
    InfoVariables repite "Definición" 3 veces; con `DictReader` se pisarían — por
    eso se leen por posición y se desambiguan."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in raw:
        key = (h or "").strip() or "col"
        if key in seen:
            seen[key] += 1
            key = f"{key}_{seen[key]}"
        else:
            seen[key] = 0
        out.append(key)
    return out


def _read_csv(path: str) -> DocTable:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                reader = csv.reader(fh)
                raw = next(reader, [])
                headers = _dedup_headers([str(c) for c in raw])
                rows = [
                    {headers[j]: (r[j] if j < len(r) else "") for j in range(len(headers))}
                    for r in reader
                ]
            return DocTable(headers, rows)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"no se pudo decodificar el CSV: {path}")


def list_sheets(path: str) -> list[str]:
    """Nombres de hoja de un XLSX (para elegir cuál importar)."""
    import openpyxl  # noqa: PLC0415 — lazy: solo donde hay xlsx
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _read_xlsx(path: str, sheet: str | None, header_row: int = 1) -> DocTable:
    import openpyxl  # noqa: PLC0415
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        rows_iter = ws.iter_rows(values_only=True)
        # Avanza hasta la fila de cabecera (1-indexed).
        header: list[str] = []
        for i, row in enumerate(rows_iter, start=1):
            if i == header_row:
                header = [("" if c is None else str(c).strip()) for c in row]
                break
        # Cabeceras únicas (preserva posición: Definición, Definición_1, …).
        headers = _dedup_headers(header)
        rows: list[dict] = []
        for row in rows_iter:
            values = ["" if c is None else c for c in row]
            rows.append({headers[j]: values[j] for j in range(min(len(headers), len(values)))})
        return DocTable(headers, rows, sheet=ws.title)
    finally:
        wb.close()


def read_table(path: str, sheet: str | None = None, header_row: int = 1) -> DocTable:
    """Lee un CSV o XLSX → (headers, rows). `sheet`/`header_row` solo para xlsx."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return _read_csv(path)
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path, sheet, header_row)
    raise ValueError(f"formato no soportado (usa .csv o .xlsx): {path}")


def forward_fill(rows: list[dict], columns: list[str]) -> list[dict]:
    """Rellena hacia abajo las columnas dadas (jerarquía apilada de InfoVariables:
    Dimensión/Criterio/Subcriterio solo vienen en la 1ª fila del grupo)."""
    last: dict[str, str] = {}
    out: list[dict] = []
    for r in rows:
        r = dict(r)
        for c in columns:
            val = str(r.get(c) or "").strip()
            if val:
                last[c] = val
            elif c in last:
                r[c] = last[c]
        out.append(r)
    return out
