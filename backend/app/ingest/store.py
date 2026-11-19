"""接入批次包的内存仓库与文件校验规则。

设计要点（对应业务口径）：
- 一个接入批次包归属于一个已登记的分析数据集、一个来源渠道，包里可带客户文件与订单文件。
- 客户号只有和渠道拼在一起才唯一，因此关联键固定为（渠道 + 客户号）。
- 每个文件保留来源渠道、批次归属、表头、校验明细；内容哈希一致的重复文件只保留一份有效记录。
- 客户与订单的关联只允许在同一个批次包内配对，缺渠道列的订单文件整文件不合格，
  不会去借用其他批次的客户/订单数据。
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CUSTOMER_KIND = "customer"
ORDER_KIND = "order"
KIND_LABELS = {CUSTOMER_KIND: "客户文件", ORDER_KIND: "订单文件"}

# 表头口径：顺序即标准列序，校验时同时检查缺列、多列与列序。
CUSTOMER_COLUMNS = ["渠道", "客户号", "客户名称", "注册日期", "会员等级"]
ORDER_COLUMNS = ["渠道", "客户号", "订单号", "下单时间", "订单金额"]
SCHEMAS: dict[str, list[str]] = {CUSTOMER_KIND: CUSTOMER_COLUMNS, ORDER_KIND: ORDER_COLUMNS}

# 字段类型口径：text/date/datetime/number。
COLUMN_TYPES: dict[str, dict[str, str]] = {
    CUSTOMER_KIND: {
        "渠道": "text",
        "客户号": "text",
        "客户名称": "text",
        "注册日期": "date",
        "会员等级": "text",
    },
    ORDER_KIND: {
        "渠道": "text",
        "客户号": "text",
        "订单号": "text",
        "下单时间": "datetime",
        "订单金额": "number",
    },
}
JOIN_KEYS = ["渠道", "客户号"]


@dataclass
class ValidationIssue:
    """一条校验明细：表头问题行号记 0，数据行问题记数据行号（从 1 开始）。"""

    row_no: int | str
    column: str
    problem: str
    value: str = ""
    level: str = "错误"

    def to_dict(self) -> dict[str, Any]:
        return {"行号": self.row_no, "字段": self.column, "问题": self.problem,
                "取值": self.value, "级别": self.level}


@dataclass
class ParsedFile:
    kind: str
    header: list[str]
    data_rows: list[list[str]]
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def header_ok(self) -> bool:
        return not any(i.row_no == "表头" for i in self.issues)


def _normalize_text(content: str) -> str:
    """统一换行并去掉 BOM，保证同一内容反复上传得到同一哈希。"""
    if content.startswith("\ufeff"):
        content = content.lstrip("\ufeff")
    return content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def content_hash(content: str) -> str:
    return hashlib.sha256(_normalize_text(content).encode("utf-8")).hexdigest()


def _parse_value(value: str, value_type: str) -> tuple[bool, Any, str]:
    """按字段类型解析单元格，返回（是否通过, 解析后的值, 失败原因）。"""
    text = (value or "").strip()
    if value_type == "text":
        if not text:
            return False, None, "文本字段为空"
        return True, text, ""
    if not text:
        return False, None, f"{value_type} 字段为空"
    if value_type == "number":
        try:
            number = float(text)
        except ValueError:
            return False, None, f"数值格式不正确：{text}"
        if number != number or number in (float("inf"), float("-inf")):
            return False, None, "数值不允许为 NaN/无穷大"
        return True, number, ""
    if value_type == "date":
        try:
            return True, datetime.strptime(text, "%Y-%m-%d").date().isoformat(), ""
        except ValueError:
            return False, None, f"日期格式应为 YYYY-MM-DD：{text}"
    if value_type == "datetime":
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return True, datetime.strptime(text, pattern).strftime("%Y-%m-%d %H:%M:%S"), ""
            except ValueError:
                continue
        return False, None, f"时间格式应为 YYYY-MM-DD HH:MM:SS：{text}"
    return True, text, ""


def parse_and_validate(kind: str, content: str, batch_channel: str) -> ParsedFile:
    """解析 CSV 表头并完成表头、关联键、字段类型校验。"""
    schema = SCHEMAS[kind]
    parsed = ParsedFile(kind=kind, header=[], data_rows=[])
    text = _normalize_text(content)
    if not text:
        parsed.issues.append(ValidationIssue("表头", "-", "文件内容为空，无法解析表头"))
        return parsed

    reader = csv.reader(io.StringIO(text))
    raw_rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not raw_rows:
        parsed.issues.append(ValidationIssue("表头", "-", "文件只有空白内容，无法解析表头"))
        return parsed

    header = [cell.strip() for cell in raw_rows[0]]
    parsed.header = header
    parsed.data_rows = raw_rows[1:]

    expected = list(schema)
    missing = [col for col in expected if col not in header]
    extra = [col for col in header if col not in expected]
    if missing:
        parsed.issues.append(ValidationIssue("表头", "、".join(missing), f"表头缺少必填列：{'、'.join(missing)}"))
    if extra:
        parsed.issues.append(ValidationIssue("表头", "、".join(extra), f"表头出现未登记列：{'、'.join(extra)}"))
    if not missing and not extra and header != expected:
        parsed.issues.append(ValidationIssue("表头", "、".join(header), f"表头列序与标准口径不一致，应为：{'、'.join(expected)}"))

    # 表头不合格时整文件已经不能入账，不再逐行放大噪音，但数据行数仍保留用于核对。
    if not parsed.header_ok:
        return parsed

    type_map = COLUMN_TYPES[kind]
    seen_join_keys: set[tuple[str, ...]] = set()
    seen_order_nos: set[str] = set()
    for offset, raw in enumerate(parsed.data_rows, start=1):
        if len(raw) != len(header):
            parsed.issues.append(ValidationIssue(
                offset, "-", f"数据行列数 {len(raw)} 与表头列数 {len(header)} 不一致"))
            continue
        record: dict[str, Any] = {}
        for col, raw_value in zip(header, raw):
            ok, parsed_value, reason = _parse_value(raw_value, type_map[col])
            if not ok:
                parsed.issues.append(ValidationIssue(offset, col, reason, raw_value.strip()))
                continue
            record[col] = parsed_value
        if len(record) != len(header):
            continue

        # 渠道归属校验：行内渠道必须与批次包的来源渠道一致，避免跨渠道串号。
        if record.get("渠道") and batch_channel and record["渠道"] != batch_channel:
            parsed.issues.append(ValidationIssue(
                offset, "渠道", f"行内渠道「{record['渠道']}」与批次来源渠道「{batch_channel}」不一致",
                str(record["渠道"])))

        if kind == CUSTOMER_KIND:
            # 客户表：关联键（渠道、客户号）必须唯一，客户号单看可以跨渠道重复。
            join_key = tuple(str(record.get(key, "")) for key in JOIN_KEYS)
            if join_key in seen_join_keys:
                parsed.issues.append(ValidationIssue(
                    offset, "、".join(JOIN_KEYS),
                    f"关联键（{'、'.join(JOIN_KEYS)}）在文件内重复：{'-'.join(join_key)}"))
            else:
                seen_join_keys.add(join_key)
        else:
            # 订单表：同一客户可以有多张订单，唯一性由（渠道、订单号）保证；
            # 关联键（渠道、客户号）只要求非空，重复是正常的一对多关系。
            order_key = (str(record.get("渠道", "")), str(record.get("订单号", "")))
            if order_key in seen_order_nos:
                parsed.issues.append(ValidationIssue(
                    offset, "订单号", f"订单号在同渠道内重复：{order_key[1]}"))
            else:
                seen_order_nos.add(order_key)

    return parsed


class IngestStore:
    """接入批次、文件、校验明细与有效数据行的内存仓库。"""

    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []
        self.files: list[dict[str, Any]] = []
        # 只有合格的根文件才持有数据行；重复文件不复制数据，保证一份内容只算一份记录。
        self.rows_by_file: dict[int, list[dict[str, Any]]] = {}
        self._batch_seq = 0
        self._file_seq = 0

    def reset(self) -> None:
        self.batches.clear()
        self.files.clear()
        self.rows_by_file.clear()
        self._batch_seq = 0
        self._file_seq = 0

    def next_batch_id(self) -> int:
        self._batch_seq += 1
        return self._batch_seq

    def next_file_id(self) -> int:
        self._file_seq += 1
        return self._file_seq

    def find_duplicate(self, *, dataset_id: int, kind: str, digest: str) -> dict[str, Any] | None:
        for entry in self.files:
            if (entry["数据集id"] == dataset_id and entry["文件类型"] == KIND_LABELS[kind]
                    and entry["内容哈希"] == digest):
                return entry
        return None

    def root_file(self, entry: dict[str, Any]) -> dict[str, Any]:
        """重复文件沿 根文件编号 找到最初那份持有数据的文件。"""
        current = entry
        while current.get("根文件id"):
            parent = next((f for f in self.files if f["id"] == current["根文件id"]), None)
            if parent is None:
                break
            current = parent
        return current

    def files_of_batch(self, batch_id: int) -> list[dict[str, Any]]:
        return [f for f in self.files if f["批次id"] == batch_id]

    def batches_of_dataset(self, dataset_id: int) -> list[dict[str, Any]]:
        return [b for b in self.batches if b["数据集id"] == dataset_id]


ingest_store = IngestStore()
