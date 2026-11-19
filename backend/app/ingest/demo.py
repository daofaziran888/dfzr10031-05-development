"""标准核对演示场景：两渠道同客户号 + 缺渠道列订单 + 补齐后重新导入。"""
from __future__ import annotations

from typing import Any

from app.ingest.service import CUSTOMER_KIND, ORDER_KIND, ingest_service
from app.store import store as main_store

CHANNEL_A = "渠道A"
CHANNEL_B = "渠道B"

CUSTOMER_A = """渠道,客户号,客户名称,注册日期,会员等级
渠道A,C001,甲客户,2026-01-05,金卡
渠道A,C002,乙客户,2026-02-11,银卡
渠道A,C003,丙客户,2026-03-20,普通"""

# 订单文件故意缺「渠道」列：表头校验直接判不合格，整文件不入账。
ORDER_A_MISSING_CHANNEL = """客户号,订单号,下单时间,订单金额
C001,SO-A-1,2026-10-01 09:10:00,120.50
C002,SO-A-2,2026-10-02 14:30:00,300.00"""

CUSTOMER_B = """渠道,客户号,客户名称,注册日期,会员等级
渠道B,C001,甲客户同名号,2026-04-08,金卡
渠道B,C004,丁客户,2026-05-16,普通"""

ORDER_B = """渠道,客户号,订单号,下单时间,订单金额
渠道B,C001,SO-B-1,2026-10-05 10:00:00,210.00
渠道B,C004,SO-B-2,2026-10-06 16:45:00,99.90
渠道B,C004,SO-B-3,2026-10-08 11:20:00,58.10"""

# 补齐渠道列后的订单文件：渠道A 重新导入，关联汇总随之可用。
ORDER_A_FIXED = """渠道,客户号,订单号,下单时间,订单金额
渠道A,C001,SO-A-1,2026-10-01 09:10:00,120.50
渠道A,C002,SO-A-2,2026-10-02 14:30:00,300.00
渠道A,C003,SO-A-3,2026-10-09 19:05:00,88.80"""


def _ingest(batch_id: int, kind: str, name: str, content: str) -> dict[str, Any]:
    entry, message = ingest_service.ingest_file(
        batch_id=batch_id, kind=kind, filename=name, content=content)
    if entry is None:
        raise RuntimeError(message)
    return entry


def build_demo(dataset_id: int) -> dict[str, Any]:
    """装入 3 个批次，返回批次与文件结果，供接口层直接核对。"""
    from app.ingest.store import ingest_store

    dataset = main_store.find("dataset", int(dataset_id))
    if dataset is None:
        raise ValueError(f"分析数据集 {dataset_id} 不存在，先在数据集管理中登记")
    ingest_store.reset()

    created: list[dict[str, Any]] = []

    batch1, _ = ingest_service.create_batch(
        dataset_id=dataset_id, channel=CHANNEL_A, remark="渠道A首次送数：订单缺渠道列")
    f1 = _ingest(batch1["id"], CUSTOMER_KIND, "channel_a_customers.csv", CUSTOMER_A)
    f2 = _ingest(batch1["id"], ORDER_KIND, "channel_a_orders_bad.csv", ORDER_A_MISSING_CHANNEL)
    created.append({"批次": dict(batch1), "文件": [f1, f2]})

    batch2, _ = ingest_service.create_batch(
        dataset_id=dataset_id, channel=CHANNEL_B, remark="渠道B首次送数：客户订单均合格")
    f3 = _ingest(batch2["id"], CUSTOMER_KIND, "channel_b_customers.csv", CUSTOMER_B)
    f4 = _ingest(batch2["id"], ORDER_KIND, "channel_b_orders.csv", ORDER_B)
    created.append({"批次": dict(batch2), "文件": [f3, f4]})

    batch3, _ = ingest_service.create_batch(
        dataset_id=dataset_id, channel=CHANNEL_A, remark="渠道A重导：客户文件内容一致去重，订单补齐渠道列")
    f5 = _ingest(batch3["id"], CUSTOMER_KIND, "channel_a_customers_retry.csv", CUSTOMER_A)
    f6 = _ingest(batch3["id"], ORDER_KIND, "channel_a_orders_fixed.csv", ORDER_A_FIXED)
    created.append({"批次": dict(batch3), "文件": [f5, f6]})

    return {
        "ok": True,
        "message": "标准核对场景已装入：渠道A缺列批次、渠道B合格批次、渠道A补齐重导批次",
        "数据集": {"id": dataset["id"], "数据集编号": dataset.get("数据集编号"), "数据集名称": dataset.get("数据集名称")},
        "批次文件结果": [
            {
                "批次编号": item["批次"]["批次编号"],
                "来源渠道": item["批次"]["来源渠道"],
                "批次状态": item["批次"]["批次状态"],
                "文件": [
                    {
                        "文件编号": f["文件编号"],
                        "文件名称": f["文件名称"],
                        "文件类型": f["文件类型"],
                        "校验结论": f["校验结论"],
                        "入账状态": f["入账状态"],
                        "新增有效行数": f["新增有效行数"],
                        "根文件编号": f["根文件编号"],
                    }
                    for f in item["文件"]
                ],
            }
            for item in created
        ],
    }
