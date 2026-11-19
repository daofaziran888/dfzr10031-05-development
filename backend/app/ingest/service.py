"""接入批次包业务规则：接收、去重、批次内配对取数。"""
from __future__ import annotations

from typing import Any

from app.ingest.store import (
    CUSTOMER_KIND,
    JOIN_KEYS,
    KIND_LABELS,
    ORDER_KIND,
    SCHEMAS,
    IngestStore,
    content_hash,
    ingest_store,
    parse_and_validate,
)
from app.store import store as main_store

DATASET_MODULE = "dataset"


class IngestService:
    def __init__(self, repo: IngestStore | None = None) -> None:
        self.repo = repo or ingest_store

    # ------------------------------------------------------------------ 批次
    def list_batches(self, *, dataset_id: int | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        batches = list(self.repo.batches)
        if dataset_id is not None:
            batches = [b for b in batches if b["数据集id"] == dataset_id]
        if channel:
            batches = [b for b in batches if b["来源渠道"] == channel]
        return sorted(batches, key=lambda b: b["id"], reverse=True)

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        batch = next((b for b in self.repo.batches if b["id"] == batch_id), None)
        if batch is None:
            return None
        result = dict(batch)
        result["文件列表"] = [self._file_view(f) for f in self.repo.files_of_batch(batch_id)]
        return result

    def create_batch(self, *, dataset_id: int, channel: str, remark: str = "") -> tuple[dict[str, Any] | None, str]:
        dataset = main_store.find(DATASET_MODULE, int(dataset_id))
        if dataset is None:
            return None, f"分析数据集 {dataset_id} 不存在，无法创建接入批次包"
        channel = (channel or "").strip()
        if not channel:
            return None, "来源渠道不能为空：客户号只有带上渠道才唯一"
        batch_id = self.repo.next_batch_id()
        batch = {
            "id": batch_id,
            "批次编号": f"INGB-{batch_id:04d}",
            "数据集id": dataset_id,
            "数据集编号": dataset.get("数据集编号"),
            "数据集名称": dataset.get("数据集名称"),
            "来源渠道": channel,
            "接收时间": _now(),
            "备注": remark,
            "批次状态": "待接收",
            "文件数": 0,
            "合格文件数": 0,
            "不合格文件数": 0,
            "重复文件数": 0,
            "新增有效记录数": 0,
        }
        self.repo.batches.append(batch)
        return batch, ""

    def _refresh_batch(self, batch: dict[str, Any]) -> None:
        files = self.repo.files_of_batch(batch["id"])
        batch["文件数"] = len(files)
        batch["合格文件数"] = sum(1 for f in files if f["校验结论"] == "合格")
        batch["不合格文件数"] = sum(1 for f in files if f["校验结论"] == "不合格")
        batch["重复文件数"] = sum(1 for f in files if f["校验结论"].startswith("重复"))
        batch["新增有效记录数"] = sum(int(f.get("新增有效行数", 0)) for f in files)
        if not files:
            batch["批次状态"] = "待接收"
        elif batch["不合格文件数"] == 0:
            batch["批次状态"] = "全部合格"
        else:
            batch["批次状态"] = "部分不合格"

    # ------------------------------------------------------------------ 文件
    def ingest_file(self, *, batch_id: int, kind: str, filename: str, content: str) -> tuple[dict[str, Any] | None, str]:
        batch = next((b for b in self.repo.batches if b["id"] == batch_id), None)
        if batch is None:
            return None, f"接入批次 {batch_id} 不存在"
        if kind not in KIND_LABELS:
            return None, f"文件类型 {kind} 不支持，仅支持 customer（客户文件）/ order（订单文件）"
        filename = (filename or "").strip() or f"{KIND_LABELS[kind]}.csv"
        digest = content_hash(content)

        parsed = parse_and_validate(kind, content, batch["来源渠道"])
        qualified = parsed.header_ok and not parsed.issues
        issues = [issue.to_dict() for issue in parsed.issues]

        duplicate = self.repo.find_duplicate(
            dataset_id=batch["数据集id"], kind=kind, digest=digest)

        file_id = self.repo.next_file_id()
        entry: dict[str, Any] = {
            "id": file_id,
            "文件编号": f"INGF-{file_id:04d}",
            "批次id": batch_id,
            "批次编号": batch["批次编号"],
            "数据集id": batch["数据集id"],
            "来源渠道": batch["来源渠道"],
            "文件类型": KIND_LABELS[kind],
            "文件名称": filename,
            "接收时间": _now(),
            "解析表头": parsed.header,
            "标准表头": list(SCHEMAS[kind]),
            "数据行数": len(parsed.data_rows),
            "内容哈希": digest,
            "校验明细": issues,
            "根文件id": None,
            "根文件编号": None,
            "根批次编号": None,
            "新增有效行数": 0,
        }

        if duplicate is not None:
            # 内容一致：无论之前那份是否合格，都不重复计算有效记录。
            root = self.repo.root_file(duplicate)
            entry["根文件id"] = root["id"]
            entry["根文件编号"] = root["文件编号"]
            entry["根批次编号"] = root["批次编号"]
            root_qualified = root["校验结论"] == "合格"
            entry["校验结论"] = "重复-合格" if root_qualified and qualified else "重复-不合格"
            entry["入账状态"] = "重复未入账"
            entry["重复说明"] = (
                f"与{duplicate['批次编号']}的{duplicate['文件名称']}内容哈希一致，"
                f"只保留一份有效记录，本文件不重复计数")
            self.repo.files.append(entry)
            self._refresh_batch(batch)
            return entry, ""

        if not qualified:
            entry["校验结论"] = "不合格"
            entry["入账状态"] = "不合格未入账"
            entry["新增有效行数"] = 0
            self.repo.files.append(entry)
            self._refresh_batch(batch)
            return entry, ""

        # 合格且非重复：解析数据行入账（此时 parse 已保证每行都能通过类型校验）。
        rows = self._materialize_rows(kind, parsed, batch, entry)
        self.repo.rows_by_file[file_id] = rows
        entry["校验结论"] = "合格"
        entry["入账状态"] = "有效"
        entry["新增有效行数"] = len(rows)
        self.repo.files.append(entry)
        self._refresh_batch(batch)
        return entry, ""

    def _materialize_rows(
        self, kind: str, parsed: Any, batch: dict[str, Any], entry: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from app.ingest.store import COLUMN_TYPES, _parse_value

        rows: list[dict[str, Any]] = []
        for line_no, raw in enumerate(parsed.data_rows, start=1):
            record: dict[str, Any] = {"数据行号": line_no}
            for col, raw_value in zip(parsed.header, raw):
                ok, value, _ = _parse_value(raw_value, COLUMN_TYPES[kind][col])
                record[col] = value if ok else raw_value.strip()
            record["来源渠道"] = batch["来源渠道"]
            record["批次id"] = batch["id"]
            record["批次编号"] = batch["批次编号"]
            record["来源文件id"] = entry["id"]
            record["来源文件名称"] = entry["文件名称"]
            rows.append(record)
        return rows

    def list_files(self, *, batch_id: int | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        files = list(self.repo.files)
        if batch_id is not None:
            files = [f for f in files if f["批次id"] == batch_id]
        if kind:
            label = KIND_LABELS.get(kind, kind)
            files = [f for f in files if f["文件类型"] == label]
        return [self._file_view(f) for f in sorted(files, key=lambda f: f["id"], reverse=True)]

    def get_file(self, file_id: int) -> dict[str, Any] | None:
        entry = next((f for f in self.repo.files if f["id"] == file_id), None)
        return self._file_view(entry, include_rows=True) if entry else None

    def _file_view(self, entry: dict[str, Any], *, include_rows: bool = False) -> dict[str, Any]:
        view = dict(entry)
        view["校验明细数"] = len(entry.get("校验明细", []))
        if include_rows:
            root = self.repo.root_file(entry)
            view["有效数据行"] = list(self.repo.rows_by_file.get(root["id"], []))
            view["根文件名称"] = root["文件名称"]
        return view

    # ------------------------------------------------------------- OLAP 取数
    def _latest_qualified_customer(self, *, dataset_id: int, channel: str) -> dict[str, Any] | None:
        """客户单表口径：该渠道最新一份合格（根）客户文件，与订单批次互不干扰。"""
        candidates = [
            f for f in self.repo.files
            if f["数据集id"] == dataset_id and f["来源渠道"] == channel
            and f["文件类型"] == KIND_LABELS[CUSTOMER_KIND]
            and self.repo.root_file(f)["校验结论"] == "合格"
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda f: f["id"])[-1]

    def customer_table(self, *, dataset_id: int, channel: str | None = None) -> dict[str, Any]:
        channels = self._channels(dataset_id, channel)
        items: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for current in channels:
            entry = self._latest_qualified_customer(dataset_id=dataset_id, channel=current)
            if entry is None:
                blocked.append({"来源渠道": current, "原因": "该渠道没有合格的客户文件可供查询"})
                continue
            root = self.repo.root_file(entry)
            rows = self.repo.rows_by_file.get(root["id"], [])
            for row in rows:
                item = dict(row)
                item["实际采用文件"] = root["文件名称"]
                item["实际采用批次"] = root["批次编号"]
                if entry["id"] != root["id"]:
                    item["引用文件"] = entry["文件名称"]
                    item["引用批次"] = entry["批次编号"]
                items.append(item)
            sources.append({
                "来源渠道": current,
                "采用批次": root["批次编号"],
                "采用文件": root["文件名称"],
                "文件编号": root["文件编号"],
                "引用批次": entry["批次编号"] if entry["id"] != root["id"] else None,
                "引用文件": entry["文件名称"] if entry["id"] != root["id"] else None,
                "记录数": len(rows),
                "关联键": "、".join(JOIN_KEYS),
            })
        return {
            "查询": "客户单表",
            "数据集id": dataset_id,
            "渠道过滤": channel,
            "取数口径": "每个渠道取最新一份合格客户文件（重复文件回链到根文件，不重复计数）",
            "实际采用来源": sources,
            "阻塞渠道": blocked,
            "items": items,
            "total": len(items),
        }

    def order_summary(self, *, dataset_id: int, channel: str | None = None) -> dict[str, Any]:
        """订单关联汇总：只在同一个批次包内配对客户与订单，绝不跨批次混用。"""
        channels = self._channels(dataset_id, channel)
        items: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for current in channels:
            channel_batches = sorted(
                (b for b in self.repo.batches_of_dataset(dataset_id) if b["来源渠道"] == current),
                key=lambda b: b["id"], reverse=True)
            chosen: dict[str, Any] | None = None
            skip_notes: list[dict[str, Any]] = []
            for batch in channel_batches:
                files = self.repo.files_of_batch(batch["id"])
                order_file = next((f for f in files if f["文件类型"] == KIND_LABELS[ORDER_KIND]), None)
                customer_file = next((f for f in files if f["文件类型"] == KIND_LABELS[CUSTOMER_KIND]), None)
                if order_file is None:
                    skip_notes.append({"批次编号": batch["批次编号"], "原因": "批次内没有订单文件"})
                    continue
                order_root = self.repo.root_file(order_file)
                if order_root["校验结论"] != "合格":
                    problems = "；".join(
                        f"{d.get('字段') or '-'}：{d.get('问题')}" for d in order_file.get("校验明细", [])[:3])
                    skip_notes.append({
                        "批次编号": batch["批次编号"],
                        "原因": f"订单文件「{order_file['文件名称']}」不合格，不能参与关联汇总（{problems}）",
                    })
                    continue
                if customer_file is None:
                    skip_notes.append({"批次编号": batch["批次编号"], "原因": "批次内缺少客户文件，无法在同批次内配对关联键"})
                    continue
                customer_root = self.repo.root_file(customer_file)
                if customer_root["校验结论"] != "合格":
                    skip_notes.append({"批次编号": batch["批次编号"], "原因": "批次内客户文件不合格，无法在同批次内配对关联键"})
                    continue
                chosen = {
                    "batch": batch,
                    "order_file": order_file,
                    "order_root": order_root,
                    "customer_file": customer_file,
                    "customer_root": customer_root,
                }
                break

            if chosen is None:
                detail = "；".join(note["原因"] for note in skip_notes)
                blocked.append({
                    "来源渠道": current,
                    "原因": "没有任何批次能在同批次内同时提供合格的客户文件与订单文件，已跳过跨批次混用",
                    "明细": detail,
                    "跳过批次": skip_notes,
                })
                continue

            customer_rows = self.repo.rows_by_file.get(chosen["customer_root"]["id"], [])
            order_rows = self.repo.rows_by_file.get(chosen["order_root"]["id"], [])
            customer_index = {
                tuple(str(row.get(key, "")) for key in JOIN_KEYS): row for row in customer_rows
            }
            total_amount = 0.0
            order_count = 0
            matched_customers: set[tuple[str, ...]] = set()
            unmatched = 0
            for order in order_rows:
                key = tuple(str(order.get(key, "")) for key in JOIN_KEYS)
                order_count += 1
                total_amount += float(order["订单金额"])
                if key in customer_index:
                    matched_customers.add(key)
                else:
                    unmatched += 1
            items.append({
                "来源渠道": current,
                "采用批次": chosen["batch"]["批次编号"],
                "订单数": order_count,
                "订单总金额": round(total_amount, 2),
                "关联上的客户数": len(matched_customers),
                "未关联订单数": unmatched,
                "客户文件": chosen["customer_root"]["文件名称"],
                "订单文件": chosen["order_root"]["文件名称"],
            })
            sources.append({
                "来源渠道": current,
                "采用批次": chosen["batch"]["批次编号"],
                "配对口径": "客户与订单严格在同一批次包内按（渠道、客户号）配对",
                "客户根文件": chosen["customer_root"]["文件名称"],
                "客户根文件编号": chosen["customer_root"]["文件编号"],
                "客户根批次": chosen["customer_root"]["批次编号"],
                "客户本批次文件": chosen["customer_file"]["文件名称"],
                "客户本批次是否重复": chosen["customer_file"]["id"] != chosen["customer_root"]["id"],
                "订单根文件": chosen["order_root"]["文件名称"],
                "订单根文件编号": chosen["order_root"]["文件编号"],
                "订单根批次": chosen["order_root"]["批次编号"],
                "订单本批次文件": chosen["order_file"]["文件名称"],
                "订单本批次是否重复": chosen["order_file"]["id"] != chosen["order_root"]["id"],
                "订单数": order_count,
                "订单总金额": round(total_amount, 2),
            })

        return {
            "查询": "订单按渠道关联汇总",
            "数据集id": dataset_id,
            "渠道过滤": channel,
            "取数口径": "逐渠道选取最新一个同批次内客户、订单均合格的批次包，禁止跨批次混用数据",
            "实际采用来源": sources,
            "阻塞渠道": blocked,
            "items": items,
            "total": len(items),
        }

    def _channels(self, dataset_id: int, channel: str | None) -> list[str]:
        if channel:
            return [channel]
        found = {b["来源渠道"] for b in self.repo.batches_of_dataset(dataset_id)}
        found.update(f["来源渠道"] for f in self.repo.files if f["数据集id"] == dataset_id)
        return sorted(found)


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ingest_service = IngestService()
