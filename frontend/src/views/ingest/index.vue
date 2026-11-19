<template>
  <section class="page" data-module="ingest">
    <header class="page-head">
      <div>
        <h2>数据集接入批次包</h2>
        <p class="page-desc">
          渠道按批次包联合报送客户与订单文件；解析表头、校验关联键（渠道+客户号）与字段类型，
          内容一致的文件只保留一份有效记录，客户与订单只在同一批次包内配对取数。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openBatchModal">新建接入批次</button>
        <button class="btn" type="button" @click="loadDemo">装入标准核对场景</button>
        <button class="btn ghost" type="button" @click="resetAll">清空接入数据</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reloadBatches">
      <label class="filter-item">
        <span>数据集 id</span>
        <input v-model="filters.dataset_id" placeholder="如 2" />
      </label>
      <label class="filter-item">
        <span>来源渠道</span>
        <input v-model="filters.channel" placeholder="如 渠道A" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置</button>
    </form>

    <h3 class="block-title">接入批次列表</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in batchColumns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in batches" :key="row.id">
          <td>{{ row['批次编号'] }}</td>
          <td>{{ row['来源渠道'] }}</td>
          <td>{{ row['数据集编号'] }}（{{ row['数据集名称'] }}）</td>
          <td><span :class="statusClass(row['批次状态'])">{{ row['批次状态'] }}</span></td>
          <td>{{ row['文件数'] }}</td>
          <td>{{ row['合格文件数'] }} / {{ row['不合格文件数'] }} / {{ row['重复文件数'] }}</td>
          <td>{{ row['新增有效记录数'] }}</td>
          <td>{{ row['接收时间'] }}</td>
          <td>{{ row['备注'] || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openBatchDetail(row.id)">批次明细</button>
            <button class="link" type="button" @click="openUpload(row.id)">上传文件</button>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td :colspan="batchColumns.length + 1" class="empty-state">暂无接入批次，可新建批次或装入标准核对场景</td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">OLAP 取数（来源随结果标注）</h3>
    <div class="olap-toolbar">
      <label class="filter-item">
        <span>查询数据集 id</span>
        <input v-model="olapDatasetId" style="width: 120px" />
      </label>
      <label class="filter-item">
        <span>渠道（可选）</span>
        <input v-model="olapChannel" placeholder="留空则全部渠道" style="width: 160px" />
      </label>
      <button class="btn primary" type="button" @click="runCustomerQuery">客户单表取数</button>
      <button class="btn primary" type="button" @click="runOrderSummary">订单按渠道关联汇总</button>
    </div>

    <div v-if="customerResult" class="result-card">
      <header class="result-head">
        <strong>{{ customerResult['查询'] }}</strong>
        <span class="muted">{{ customerResult['取数口径'] }}</span>
      </header>
      <p v-if="customerResult['阻塞渠道'].length" class="warn-text">
        阻塞渠道：<template v-for="b in customerResult['阻塞渠道']" :key="b['来源渠道']">
          【{{ b['来源渠道'] }}：{{ b['原因'] }}】
        </template>
      </p>
      <ul class="source-list">
        <li v-for="s in customerResult['实际采用来源']" :key="s['来源渠道']">
          渠道「{{ s['来源渠道'] }}」采用 {{ s['采用批次'] }} 的 {{ s['采用文件'] }}
          （{{ s['记录数'] }} 条）<template v-if="s['引用文件']">，重复件来自 {{ s['引用批次'] }} 的 {{ s['引用文件'] }}</template>
        </li>
      </ul>
      <table class="data-table">
        <thead>
          <tr><th>渠道</th><th>客户号</th><th>客户名称</th><th>注册日期</th><th>会员等级</th><th>实际采用批次</th><th>实际采用文件</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in customerResult.items" :key="idx">
            <td>{{ row['来源渠道'] }}</td>
            <td>{{ row['客户号'] }}</td>
            <td>{{ row['客户名称'] }}</td>
            <td>{{ row['注册日期'] }}</td>
            <td>{{ row['会员等级'] }}</td>
            <td>{{ row['实际采用批次'] }}</td>
            <td>{{ row['实际采用文件'] }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="orderResult" class="result-card">
      <header class="result-head">
        <strong>{{ orderResult['查询'] }}</strong>
        <span class="muted">{{ orderResult['取数口径'] }}</span>
      </header>
      <div v-if="orderResult['阻塞渠道'].length" class="warn-text">
        <p v-for="b in orderResult['阻塞渠道']" :key="b['来源渠道']" style="margin:4px 0">
          阻塞渠道「{{ b['来源渠道'] }}」：{{ b['原因'] }}<br />
          <span v-for="n in b['跳过批次']" :key="n['批次编号']" class="muted">
            · 跳过 {{ n['批次编号'] }}：{{ n['原因'] }}<br />
          </span>
        </p>
      </div>
      <ul class="source-list">
        <li v-for="s in orderResult['实际采用来源']" :key="s['来源渠道']">
          渠道「{{ s['来源渠道'] }}」采用批次 {{ s['采用批次'] }}；
          客户根文件 {{ s['客户根批次'] }}/{{ s['客户根文件'] }}
          <em v-if="s['客户本批次是否重复']">（本批次 {{ s['客户本批次文件'] }} 为重复件，不重复计数）</em>；
          订单根文件 {{ s['订单根批次'] }}/{{ s['订单根文件'] }}
          <em v-if="s['订单本批次是否重复']">（本批次为重复件）</em>
        </li>
      </ul>
      <table class="data-table">
        <thead>
          <tr><th>渠道</th><th>采用批次</th><th>订单数</th><th>订单总金额</th><th>关联客户数</th><th>未关联订单</th><th>客户文件</th><th>订单文件</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in orderResult.items" :key="idx">
            <td>{{ row['来源渠道'] }}</td>
            <td>{{ row['采用批次'] }}</td>
            <td>{{ row['订单数'] }}</td>
            <td>{{ row['订单总金额'] }}</td>
            <td>{{ row['关联上的客户数'] }}</td>
            <td>{{ row['未关联订单数'] }}</td>
            <td>{{ row['客户文件'] }}</td>
            <td>{{ row['订单文件'] }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="page-foot">
      <span v-if="message" :class="messageError ? 'error-text' : 'ok-text'">{{ message }}</span>
    </footer>

    <!-- 新建批次 -->
    <Teleport to="body">
      <div v-if="showBatchModal" class="modal-overlay" @click.self="showBatchModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>新建接入批次包</h3>
            <button class="btn ghost" type="button" @click="showBatchModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitBatch">
            <label class="modal-field">
              <span>分析数据集 id（数据集管理中已登记）</span>
              <input v-model="batchForm.dataset_id" required placeholder="如 2" />
            </label>
            <label class="modal-field">
              <span>来源渠道</span>
              <input v-model="batchForm.channel" required placeholder="如 渠道A；客户号带上渠道才唯一" />
            </label>
            <label class="modal-field">
              <span>备注</span>
              <input v-model="batchForm.remark" placeholder="可选" />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showBatchModal = false">取消</button>
              <button class="btn primary" type="submit">创建批次</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 上传文件 -->
    <Teleport to="body">
      <div v-if="showUploadModal" class="modal-overlay" @click.self="showUploadModal = false">
        <div class="modal-card" style="width: 640px">
          <header class="modal-head">
            <h3>向批次 {{ uploadBatchNo }} 上传文件</h3>
            <button class="btn ghost" type="button" @click="showUploadModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitUpload">
            <label class="modal-field">
              <span>文件类型</span>
              <select v-model="uploadForm.kind">
                <option value="customer">客户文件（渠道,客户号,客户名称,注册日期,会员等级）</option>
                <option value="order">订单文件（渠道,客户号,订单号,下单时间,订单金额）</option>
              </select>
            </label>
            <label class="modal-field">
              <span>文件名称</span>
              <input v-model="uploadForm.filename" required />
            </label>
            <label class="modal-field">
              <span>选择本地 CSV（自动读入内容）</span>
              <input type="file" accept=".csv,text/csv" @change="onPickFile" />
            </label>
            <label class="modal-field">
              <span>或直接粘贴 CSV 文本（首行表头）</span>
              <textarea v-model="uploadForm.content" rows="8" required style="font-family: monospace"></textarea>
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showUploadModal = false">取消</button>
              <button class="btn primary" type="submit">上传并校验</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 批次/文件明细 -->
    <Teleport to="body">
      <div v-if="detail" class="modal-overlay" @click.self="detail = null">
        <div class="modal-card" style="width: 820px">
          <header class="modal-head">
            <h3>{{ detail['批次编号'] }}（{{ detail['来源渠道'] }}）文件明细</h3>
            <button class="btn ghost" type="button" @click="detail = null">&times;</button>
          </header>
          <div class="modal-body">
            <table class="data-table" v-for="f in detail['文件列表']" :key="f.id" style="margin-bottom: 14px">
              <tbody>
                <tr><th style="width: 130px">文件</th><td>{{ f['文件编号'] }} · {{ f['文件名称'] }}（{{ f['文件类型'] }}）</td></tr>
                <tr><th>校验结论</th><td><span :class="statusClass(f['校验结论'])">{{ f['校验结论'] }}</span> · {{ f['入账状态'] }} · 新增有效行 {{ f['新增有效行数'] }} / 数据行 {{ f['数据行数'] }}</td></tr>
                <tr><th>来源/批次归属</th><td>来源渠道 {{ f['来源渠道'] }} · 批次 {{ f['批次编号'] }} · 哈希 {{ f['内容哈希'].slice(0, 12) }}…</td></tr>
                <tr v-if="f['根文件编号']"><th>去重回链</th><td>{{ f['重复说明'] }}</td></tr>
                <tr><th>解析表头</th><td>{{ f['解析表头'].join('、') || '—' }}</td></tr>
                <tr><th>标准表头</th><td>{{ f['标准表头'].join('、') }}</td></tr>
                <tr v-if="f['校验明细'].length"><th>校验明细</th><td>
                  <ul style="margin:0;padding-left:18px">
                    <li v-for="(d, i) in f['校验明细']" :key="i" class="error-text">
                      行{{ d['行号'] }} · {{ d['字段'] }}：{{ d['问题'] }}<span v-if="d['取值']">（取值：{{ d['取值'] }}）</span>
                    </li>
                  </ul>
                </td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type BatchRow = {
  id: number
  批次编号: string
  来源渠道: string
  数据集编号: string
  数据集名称: string
  批次状态: string
  文件数: number
  合格文件数: number
  不合格文件数: number
  重复文件数: number
  新增有效记录数: number
  接收时间: string
  备注?: string
}
type FileRow = Record<string, any>

const ENDPOINT = '/api/ingest'
const batchColumns = ['批次编号', '来源渠道', '数据集', '批次状态', '文件数', '合格/不合格/重复', '新增有效记录', '接收时间', '备注']

const batches = ref<BatchRow[]>([])
const filters = ref<Record<string, string>>({})
const message = ref('')
const messageError = ref(false)

const showBatchModal = ref(false)
const batchForm = ref({ dataset_id: '2', channel: '', remark: '' })

const showUploadModal = ref(false)
const uploadBatchId = ref<number | null>(null)
const uploadBatchNo = ref('')
const uploadForm = ref({ kind: 'customer', filename: '', content: '' })

const detail = ref<Record<string, any> | null>(null)

const olapDatasetId = ref('2')
const olapChannel = ref('')
const customerResult = ref<Record<string, any> | null>(null)
const orderResult = ref<Record<string, any> | null>(null)

const stats = computed(() => {
  const total = batches.value.length
  const partial = batches.value.filter((b) => b['批次状态'] === '部分不合格').length
  const files = batches.value.reduce<{ qualified: number; duplicate: number; bad: number; rows: number }>((acc, b) => {
    acc.qualified += Number(b['合格文件数'] || 0)
    acc.duplicate += Number(b['重复文件数'] || 0)
    acc.bad += Number(b['不合格文件数'] || 0)
    acc.rows += Number(b['新增有效记录数'] || 0)
    return acc
  }, { qualified: 0, duplicate: 0, bad: 0, rows: 0 })
  return [
    { label: '接入批次包', value: total },
    { label: '部分不合格批次', value: partial },
    { label: '合格 / 不合格 / 重复文件', value: `${files.qualified} / ${files.bad} / ${files.duplicate}` },
    { label: '累计新增有效记录', value: files.rows },
  ]
})

function statusClass(status: string) {
  if (status === '全部合格' || status === '合格' || status === '重复-合格') return 'tag tag-ok'
  if (status === '部分不合格' || status === '不合格' || status === '重复-不合格') return 'tag tag-bad'
  return 'tag tag-muted'
}

function notify(text: string, isError = false) {
  message.value = text
  messageError.value = isError
}

function resetFilters() {
  filters.value = {}
  void reloadBatches()
}

async function reloadBatches() {
  const params = new URLSearchParams()
  if (filters.value.dataset_id) params.set('dataset_id', filters.value.dataset_id)
  if (filters.value.channel) params.set('channel', filters.value.channel)
  const response = await request(`${ENDPOINT}/batches?${params.toString()}`)
  if (!response.ok) {
    notify('批次列表读取失败', true)
    return
  }
  const payload = await response.json()
  batches.value = payload.items ?? []
}

function openBatchModal() {
  batchForm.value = { dataset_id: '2', channel: '', remark: '' }
  showBatchModal.value = true
}

async function submitBatch() {
  const response = await request(`${ENDPOINT}/batches`, {
    method: 'POST',
    body: JSON.stringify({
      数据集id: Number(batchForm.value.dataset_id),
      来源渠道: batchForm.value.channel,
      备注: batchForm.value.remark,
    }),
  })
  const payload = await response.json()
  if (!response.ok) {
    notify(payload.detail || '批次创建失败', true)
    return
  }
  showBatchModal.value = false
  notify(payload.message)
  await reloadBatches()
}

function openUpload(batchId: number) {
  const row = batches.value.find((b) => Number(b.id) === batchId)
  uploadBatchId.value = batchId
  uploadBatchNo.value = String(row?.['批次编号'] || batchId)
  uploadForm.value = { kind: 'customer', filename: '', content: '' }
  showUploadModal.value = true
}

function onPickFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadForm.value.filename = file.name
  const reader = new FileReader()
  reader.onload = () => {
    uploadForm.value.content = String(reader.result || '')
  }
  reader.readAsText(file, 'utf-8')
}

async function submitUpload() {
  if (uploadBatchId.value === null) return
  const response = await request(`${ENDPOINT}/batches/${uploadBatchId.value}/files`, {
    method: 'POST',
    body: JSON.stringify({
      文件类型: uploadForm.value.kind,
      文件名称: uploadForm.value.filename,
      文件内容: uploadForm.value.content,
    }),
  })
  const payload = await response.json()
  if (!response.ok) {
    notify(payload.detail || '上传失败', true)
    return
  }
  showUploadModal.value = false
  notify(payload.message, !payload.ok)
  await reloadBatches()
}

async function openBatchDetail(batchId: number) {
  const response = await request(`${ENDPOINT}/batches/${batchId}`)
  if (!response.ok) {
    notify('批次明细读取失败', true)
    return
  }
  detail.value = await response.json()
}

async function runCustomerQuery() {
  orderResult.value = null
  const params = new URLSearchParams({ dataset_id: olapDatasetId.value })
  if (olapChannel.value) params.set('channel', olapChannel.value)
  const response = await request(`${ENDPOINT}/olap/customers?${params.toString()}`)
  if (!response.ok) {
    notify('客户单表查询失败', true)
    customerResult.value = null
    return
  }
  customerResult.value = await response.json()
  notify('客户单表取数完成，结果已标注实际采用来源')
}

async function runOrderSummary() {
  customerResult.value = null
  const params = new URLSearchParams({ dataset_id: olapDatasetId.value })
  if (olapChannel.value) params.set('channel', olapChannel.value)
  const response = await request(`${ENDPOINT}/olap/order-summary?${params.toString()}`)
  if (!response.ok) {
    notify('订单汇总查询失败', true)
    orderResult.value = null
    return
  }
  orderResult.value = await response.json()
  notify('订单关联汇总完成，取数严格限制在同批次配对范围内')
}

async function loadDemo() {
  const response = await request(`${ENDPOINT}/demo/load?dataset_id=${olapDatasetId.value || 2}`, { method: 'POST' })
  const payload = await response.json()
  if (!response.ok) {
    notify(payload.detail || '演示场景装入失败', true)
    return
  }
  notify(payload.message)
  await reloadBatches()
}

async function resetAll() {
  await request(`${ENDPOINT}/reset`, { method: 'POST' })
  customerResult.value = null
  orderResult.value = null
  detail.value = null
  notify('接入批次数据已清空（数据集登记信息保留）')
  await reloadBatches()
}

onMounted(reloadBatches)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.block-title { font-size: 15px; margin: 18px 0 8px; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.tag-ok { background: #e7f6ec; color: #177245; }
.tag-bad { background: #fdecec; color: #b42318; }
.tag-muted { background: #eef2f7; color: #475569; }
.olap-toolbar { display: flex; gap: 12px; align-items: flex-end; margin: 8px 0 12px; flex-wrap: wrap; }
.result-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 14px; }
.result-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 8px; }
.muted { color: var(--muted); font-size: 12px; }
.warn-text { color: #b54708; background: #fffaeb; border: 1px solid #fedf89; border-radius: 6px; padding: 8px 10px; font-size: 13px; }
.ok-text { color: #177245; }
.source-list { margin: 6px 0 10px; padding-left: 20px; font-size: 13px; }
.source-list em { color: var(--muted); font-style: normal; }
select, textarea { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
</style>
