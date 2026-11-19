# 数据分析实验与数据应用服务平台

面向指标口径管理、分析实验设计、OLAP查询、数据看板构建与数据API发布的数据应用平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度类型 |
| 数据集管理 | `dataset` | 分析数据集 | 数据集编号、数据集名称、来源类型 |
| 接入批次包 | `ingest` | 多来源接入批次/文件 | 批次编号、来源渠道、文件类型、校验结论、内容哈希 |
| 分析实验 | `experiment` | 实验方案 | 方案编号、实验名称、分析数据集 |
| OLAP查询 | `query_builder` | 查询模板 | 模板编号、模板名称、查询语句 |
| 数据看板 | `dashboard` | 看板配置 | 看板编号、看板名称、包含图表 |
| 图表配置 | `chart` | 图表 | 图表编号、图表名称、图表类型 |
| 数据API发布 | `data_service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 智能告警 | `alert` | 告警规则 | 规则编号、告警名称、监控指标 |
| 分析用户组 | `user_group` | 用户组 | 组编号、组名称、组描述 |
| 数据快照 | `snapshot` | 快照记录 | 快照编号、数据集名称、快照时间 |
| 任务调度 | `scheduler` | 调度任务 | 任务编号、任务名称、调度类型 |
| 缓存管理 | `cache` | 缓存配置 | 缓存编号、缓存目标、缓存策略 |
| 分析管道 | `pipeline` | 分析节点 | 节点编号、输入数据集、分析算子 |
| 分析模型 | `model` | 模型配置 | 模型编号、模型名称、模型类型 |
| 数据集市 | `data_mart` | 集市表 | 表编号、表名称、主题域 |
| 数据导出 | `export` | 导出任务 | 任务编号、导出对象、导出格式 |
| 数据审计 | `audit` | 审计记录 | 记录编号、操作类型、操作人 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 数据集接入批次包（`ingest`）

渠道每天报送的客户/订单 CSV 以「接入批次包」联合接收，代码在
`backend/app/ingest/`，接口前缀 `/api/ingest`，前端页面在「接入批次包」菜单。

业务口径：

- 一个批次包挂在已登记的分析数据集上并锁定一个**来源渠道**；客户号只有和渠道拼成
  关联键（渠道、客户号）才唯一，两个渠道可以出现相同客户号。
- 上传即解析表头并校验：缺列/多列/列序、关联键唯一性、字段类型
  （text/date/datetime/number）、行内渠道与批次渠道是否一致；不合格整文件不入账，
  每个文件都保留来源渠道、批次归属与逐条校验明细。
- 文件内容做 SHA-256 哈希；同数据集、同类型、内容一致的重复文件只保留一份有效记录，
  重复件回链根文件、`新增有效行数` 计 0。
- OLAP 取数只使用当前可用的合格文件：
  - `GET /api/ingest/olap/customers`：客户单表，逐渠道取最新合格客户文件，每行写明
    实际采用的批次与文件；
  - `GET /api/ingest/olap/order-summary`：订单按渠道汇总，客户与订单**严格在同一批次包
    内**按关联键配对，缺渠道列等不合格订单所在渠道直接进入「阻塞渠道」，绝不混用其他
    批次的数据；返回体的 `实际采用来源` 逐项标注根文件、批次与重复件归属。
- `POST /api/ingest/demo/load?dataset_id=2` 一键装入标准核对场景（两渠道同客户号、
  渠道A订单缺渠道列、补齐字段后重新导入）。
- 端到端核对脚本：`cd backend && .venv/bin/python scripts/verify_ingest.py`。
