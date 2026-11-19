"""端到端核对脚本：复现两渠道同客户号、缺渠道列订单、补齐重导的完整接入与 OLAP 取数。

运行（在 backend 目录）：.venv/bin/python scripts/verify_ingest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PASS = "✅"
FAIL = "❌"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = PASS if condition else FAIL
    print(f"{mark} {name}" + (f" —— {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def section(title: str) -> None:
    print(f"\n===== {title} =====")


def main() -> int:
    section("0. 健康检查与场景重置")
    health = client.get("/api/health").json()
    overview = client.get("/api/overview").json()
    check("后端服务可用且接入批次模块已加载",
          health["ok"] and any(m["name"] == "ingest" for m in overview["modules"]))
    client.post("/api/ingest/reset")

    section("1. 装入标准演示场景（渠道A缺列 / 渠道B合格 / 渠道A补齐重导）")
    demo = client.post("/api/ingest/demo/load?dataset_id=2").json()
    check("演示场景装入成功", demo["ok"], demo.get("message", ""))
    batches = client.get("/api/ingest/batches").json()["items"]
    check("共生成 3 个接入批次包", len(batches) == 3, f"实际 {len(batches)} 个")

    section("2. 批次1（渠道A）：客户文件合格，订单文件缺渠道列整文件不合格")
    batch1 = client.get("/api/ingest/batches/1").json()
    f_cust_a = next(f for f in batch1["文件列表"] if f["文件类型"] == "客户文件")
    f_ord_bad = next(f for f in batch1["文件列表"] if f["文件类型"] == "订单文件")
    check("渠道A客户文件合格并入账", f_cust_a["校验结论"] == "合格" and f_cust_a["新增有效行数"] == 3)
    check("渠道A缺列订单文件不合格、零行入账",
          f_ord_bad["校验结论"] == "不合格" and f_ord_bad["新增有效行数"] == 0)
    issue_text = "；".join(d["问题"] for d in f_ord_bad["校验明细"])
    check("校验明细明确指出缺少「渠道」列", any("渠道" in d["问题"] and "缺少必填列" in d["问题"] for d in f_ord_bad["校验明细"]), issue_text)
    check("批次1状态为部分不合格", batch1["批次状态"] == "部分不合格")
    check("文件保留来源渠道与批次归属",
          f_ord_bad["来源渠道"] == "渠道A" and f_ord_bad["批次编号"] == "INGB-0001")

    section("3. 批次2（渠道B）：与渠道A相同客户号 C001，但关联键带渠道后互不冲突")
    batch2 = client.get("/api/ingest/batches/2").json()
    f_cust_b = next(f for f in batch2["文件列表"] if f["文件类型"] == "客户文件")
    f_ord_b = next(f for f in batch2["文件列表"] if f["文件类型"] == "订单文件")
    check("渠道B客户文件合格（2 行）", f_cust_b["校验结论"] == "合格" and f_cust_b["新增有效行数"] == 2)
    check("渠道B订单文件合格（3 行，含同客户多订单）", f_ord_b["校验结论"] == "合格" and f_ord_b["新增有效行数"] == 3)

    section("4. 批次3（渠道A重导）：客户文件内容一致只算一份，补齐订单合格")
    batch3 = client.get("/api/ingest/batches/3").json()
    f_cust_dup = next(f for f in batch3["文件列表"] if f["文件类型"] == "客户文件")
    f_ord_fix = next(f for f in batch3["文件列表"] if f["文件类型"] == "订单文件")
    check("重导客户文件识别为重复且不重复计数",
          f_cust_dup["校验结论"] == "重复-合格" and f_cust_dup["新增有效行数"] == 0)
    check("重复文件回链根文件 INGF-0001", f_cust_dup["根文件编号"] == "INGF-0001", f_cust_dup.get("重复说明", ""))
    check("补齐渠道列的订单文件合格（3 行）", f_ord_fix["校验结论"] == "合格" and f_ord_fix["新增有效行数"] == 3)
    check("批次3状态为全部合格", batch3["批次状态"] == "全部合格")

    section("5. 去重总口径：全库有效客户记录只算一份内容")
    files = client.get("/api/ingest/files").json()["items"]
    valid_customer_rows = sum(f["新增有效行数"] for f in files if f["文件类型"] == "客户文件" and f["校验结论"] == "合格")
    check("客户有效记录合计 5 行（A 3 + B 2，重复文件贡献 0）", valid_customer_rows == 5, f"实际 {valid_customer_rows}")

    section("6. OLAP 客户单表：同号客户按渠道区分，逐行标注实际来源")
    cust = client.get("/api/ingest/olap/customers?dataset_id=2").json()
    check("取到 5 条客户记录", cust["total"] == 5, f"实际 {cust['total']}")
    c001_rows = [r for r in cust["items"] if r["客户号"] == "C001"]
    check("客户号 C001 因渠道不同而存在两条", len(c001_rows) == 2)
    check("两条 C001 分别归属渠道A、渠道B", {r["来源渠道"] for r in c001_rows} == {"渠道A", "渠道B"})
    a_row = next(r for r in c001_rows if r["来源渠道"] == "渠道A")
    check("渠道A客户行写明实际采用批次 INGB-0001 与来源文件",
          a_row["实际采用批次"] == "INGB-0001" and a_row["实际采用文件"] == "channel_a_customers.csv")
    source_channels = {s["来源渠道"] for s in cust["实际采用来源"]}
    check("查询结果汇总实际采用来源（两个渠道各一份）", source_channels == {"渠道A", "渠道B"})

    section("7. OLAP 订单按渠道汇总：缺列批次被阻断，绝不跨批次混用")
    # 先模拟重导之前的状态：重置后只装批次1、2
    client.post("/api/ingest/reset")
    client.post("/api/ingest/batches", json={"数据集id": 2, "来源渠道": "渠道A", "备注": "首次"})
    client.post("/api/ingest/batches/1/files",
                json={"文件类型": "customer", "文件名称": "a_c.csv", "文件内容": _demo_customer_a()})
    client.post("/api/ingest/batches/1/files",
                json={"文件类型": "order", "文件名称": "a_o_bad.csv", "文件内容": _demo_order_bad()})
    client.post("/api/ingest/batches", json={"数据集id": 2, "来源渠道": "渠道B", "备注": "首次"})
    client.post("/api/ingest/batches/2/files",
                json={"文件类型": "customer", "文件名称": "b_c.csv", "文件内容": _demo_customer_b()})
    client.post("/api/ingest/batches/2/files",
                json={"文件类型": "order", "文件名称": "b_o.csv", "文件内容": _demo_order_b()})

    summary_before = client.get("/api/ingest/olap/order-summary?dataset_id=2").json()
    blocked_a = next((b for b in summary_before["阻塞渠道"] if b["来源渠道"] == "渠道A"), None)
    check("渠道A因订单缺列被阻断、不产生汇总",
          blocked_a is not None and "不合格" in blocked_a["明细"])
    check("阻断说明记录了被跳过的批次", blocked_a is not None and any(s["批次编号"] == "INGB-0001" for s in blocked_a["跳过批次"]))
    b_item = next((i for i in summary_before["items"] if i["来源渠道"] == "渠道B"), None)
    check("渠道B正常汇总：订单数 3、金额 368.00",
          b_item is not None and b_item["订单数"] == 3 and b_item["订单总金额"] == 368.00,
          str(b_item))
    check("渠道B汇总写明同批次配对的客户/订单文件",
          b_item is not None and b_item["客户文件"] == "b_c.csv" and b_item["订单文件"] == "b_o.csv")
    check("渠道A没有借用批次1客户或其他批次数据（items 中无渠道A）",
          all(i["来源渠道"] != "渠道A" for i in summary_before["items"]))

    section("8. 渠道A补齐字段后重新导入，再次取数核对归属与同号客户")
    client.post("/api/ingest/batches", json={"数据集id": 2, "来源渠道": "渠道A", "备注": "补齐重导"})
    dup_resp = client.post("/api/ingest/batches/3/files",
                           json={"文件类型": "customer", "文件名称": "a_c_retry.csv", "文件内容": _demo_customer_a()}).json()
    fix_resp = client.post("/api/ingest/batches/3/files",
                           json={"文件类型": "order", "文件名称": "a_o_fixed.csv", "文件内容": _demo_order_fixed()}).json()
    check("重导客户文件仍判定重复、零新增", dup_resp["entry"]["校验结论"] == "重复-合格")
    check("补齐订单文件合格 3 行", fix_resp["entry"]["校验结论"] == "合格")

    cust_after = client.get("/api/ingest/olap/customers?dataset_id=2").json()
    a_cust = next(r for r in cust_after["items"] if r["来源渠道"] == "渠道A" and r["客户号"] == "C001")
    check("客户单表渠道A数据仍来自根文件（INGB-0001 的 a_c.csv），归属未被重复文件改写",
          a_cust["实际采用文件"] == "a_c.csv" and a_cust["实际采用批次"] == "INGB-0001",
          f"{a_cust.get('实际采用批次')} / {a_cust.get('实际采用文件')}")

    summary_after = client.get("/api/ingest/olap/order-summary?dataset_id=2").json()
    check("补齐后无阻塞渠道", summary_after["阻塞渠道"] == [])
    a_summary = next(i for i in summary_after["items"] if i["来源渠道"] == "渠道A")
    check("渠道A汇总采用最新同批次配对批次 INGB-0003", a_summary["采用批次"] == "INGB-0003")
    check("渠道A订单数 3、金额 509.30（120.50+300.00+88.80）",
          a_summary["订单数"] == 3 and a_summary["订单总金额"] == 509.30, str(a_summary))
    check("渠道A关联上的客户数为 3", a_summary["关联上的客户数"] == 3)
    src_a = next(s for s in summary_after["实际采用来源"] if s["来源渠道"] == "渠道A")
    check("来源标注：客户根文件回链 INGB-0001、本批次为重复件；订单根文件就在 INGB-0003",
          src_a["客户根批次"] == "INGB-0001" and src_a["客户本批次是否重复"] is True
          and src_a["订单根批次"] == "INGB-0003" and src_a["订单本批次是否重复"] is False
          and src_a["订单本批次文件"] == "a_o_fixed.csv", str(src_a))
    b_summary = next(i for i in summary_after["items"] if i["来源渠道"] == "渠道B")
    check("渠道B汇总不受渠道A重导影响（仍 368.00）", b_summary["订单总金额"] == 368.00)

    section("9. 补充校验：字段类型错误与渠道串号也会被拦截")
    bad_type = "渠道,客户号,客户名称,注册日期,会员等级\n渠道A,C009,壬客户,2026/01/01,金卡"
    client.post("/api/ingest/batches", json={"数据集id": 2, "来源渠道": "渠道A", "备注": "类型错误"})
    r = client.post("/api/ingest/batches/4/files",
                    json={"文件类型": "customer", "文件名称": "bad_type.csv", "文件内容": bad_type}).json()
    check("日期格式错误被记录且整文件不合格",
          r["entry"]["校验结论"] == "不合格" and any("日期格式" in d["问题"] for d in r["entry"]["校验明细"]))
    cross = "渠道,客户号,订单号,下单时间,订单金额\n渠道X,C001,SO-X-1,2026-10-01 09:10:00,10.00"
    client.post("/api/ingest/batches", json={"数据集id": 2, "来源渠道": "渠道A", "备注": "串号"})
    r2 = client.post("/api/ingest/batches/5/files",
                     json={"文件类型": "order", "文件名称": "cross.csv", "文件内容": cross}).json()
    check("行内渠道与批次渠道不一致被拦截",
          r2["entry"]["校验结论"] == "不合格" and any("批次来源渠道" in d["问题"] for d in r2["entry"]["校验明细"]))

    section("10. 手工接入 API 与批次清单口径")
    created = client.post("/api/ingest/batches", json={"数据集id": 2, "来源渠道": "渠道C"}).json()
    check("可手工创建批次包", created["ok"] and created["entry"]["批次编号"] == "INGB-0006")
    missing = client.post("/api/ingest/batches", json={"数据集id": 999, "来源渠道": "渠道C"})
    check("挂到不存在的数据集会被拒绝", missing.status_code == 400)

    print("\n" + "=" * 40)
    if failures:
        print(f"{FAIL} 核对未通过，失败项 {len(failures)}：{failures}")
        return 1
    print(f"{PASS} 全部核对通过：接入、去重、批次隔离与来源标注均符合口径")
    return 0


def _demo_customer_a() -> str:
    from app.ingest.demo import CUSTOMER_A
    return CUSTOMER_A


def _demo_customer_b() -> str:
    from app.ingest.demo import CUSTOMER_B
    return CUSTOMER_B


def _demo_order_bad() -> str:
    from app.ingest.demo import ORDER_A_MISSING_CHANNEL
    return ORDER_A_MISSING_CHANNEL


def _demo_order_b() -> str:
    from app.ingest.demo import ORDER_B
    return ORDER_B


def _demo_order_fixed() -> str:
    from app.ingest.demo import ORDER_A_FIXED
    return ORDER_A_FIXED


if __name__ == "__main__":
    sys.exit(main())
