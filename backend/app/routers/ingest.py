"""数据集接入批次包接口：批次接收、文件校验、去重查询与 OLAP 取数。"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.ingest.service import ingest_service
from app.ingest.store import KIND_LABELS

router = APIRouter(prefix="/api/ingest", tags=["数据集接入批次包"])


class BatchCreatePayload(BaseModel):
    数据集id: int
    来源渠道: str
    备注: str | None = ""


class FileIngestPayload(BaseModel):
    文件类型: Literal["customer", "order"]
    文件名称: str | None = None
    文件内容: str = Field(description="CSV 文本，首行表头")


@router.get("/batches")
def list_batches(
    dataset_id: int | None = Query(default=None, description="按已登记分析数据集过滤"),
    channel: str | None = Query(default=None, description="按来源渠道过滤"),
) -> dict[str, Any]:
    items = ingest_service.list_batches(dataset_id=dataset_id, channel=channel)
    return {"items": items, "total": len(items)}


@router.post("/batches")
def create_batch(payload: BatchCreatePayload) -> dict[str, Any]:
    batch, message = ingest_service.create_batch(
        dataset_id=payload.数据集id, channel=payload.来源渠道, remark=payload.备注 or "")
    if batch is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": "接入批次包已创建，等待渠道文件上传", "entry": batch}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict[str, Any]:
    batch = ingest_service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"接入批次 {batch_id} 不存在")
    return batch


@router.post("/batches/{batch_id}/files")
def ingest_file(batch_id: int, payload: FileIngestPayload) -> dict[str, Any]:
    entry, message = ingest_service.ingest_file(
        batch_id=batch_id,
        kind=payload.文件类型,
        filename=payload.文件名称 or f"{KIND_LABELS[payload.文件类型]}.csv",
        content=payload.文件内容,
    )
    if entry is None:
        raise HTTPException(status_code=400, detail=message)
    if entry["校验结论"] == "不合格":
        return {"ok": False, "message": "文件表头或数据校验未通过，整文件不入账，明细见 entry.校验明细", "entry": entry}
    if entry["校验结论"].startswith("重复"):
        return {"ok": True, "message": entry["重复说明"], "entry": entry}
    return {"ok": True, "message": f"文件校验合格，{entry['新增有效行数']} 条有效记录已入账", "entry": entry}


@router.get("/files")
def list_files(
    batch_id: int | None = Query(default=None),
    kind: Literal["customer", "order"] | None = Query(default=None),
) -> dict[str, Any]:
    items = ingest_service.list_files(batch_id=batch_id, kind=kind)
    return {"items": items, "total": len(items)}


@router.get("/files/{file_id}")
def get_file(file_id: int) -> dict[str, Any]:
    entry = ingest_service.get_file(file_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"接入文件 {file_id} 不存在")
    return entry


@router.get("/olap/customers")
def customer_table(
    dataset_id: int = Query(..., description="已登记分析数据集 id"),
    channel: str | None = Query(default=None, description="不传则逐渠道返回"),
) -> dict[str, Any]:
    """客户单表取数：按渠道取最新合格客户文件，结果逐行写明实际采用的来源。"""
    return ingest_service.customer_table(dataset_id=dataset_id, channel=channel)


@router.get("/olap/order-summary")
def order_summary(
    dataset_id: int = Query(..., description="已登记分析数据集 id"),
    channel: str | None = Query(default=None, description="不传则逐渠道返回"),
) -> dict[str, Any]:
    """订单按渠道关联汇总：仅在同一批次包内配对客户与订单，来源在返回体中逐项标注。"""
    return ingest_service.order_summary(dataset_id=dataset_id, channel=channel)


@router.post("/reset")
def reset_ingest() -> dict[str, Any]:
    """清空接入批次演示数据（只影响接入批次模块，不动数据集登记信息）。"""
    from app.ingest.store import ingest_store

    ingest_store.reset()
    return {"ok": True, "message": "接入批次数据已清空"}


@router.post("/demo/load")
def load_demo(dataset_id: int = Query(default=2, description="演示挂载的分析数据集 id，默认 DATA-0002")) -> dict[str, Any]:
    """一键装入两渠道同客户号、缺渠道列订单、补齐重导的标准核对场景。"""
    from app.ingest.demo import build_demo

    try:
        return build_demo(dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
