<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '../api/endpoints'
import StatusBadge from './StatusBadge.vue'
import InvoiceDetail from './InvoiceDetail.vue'

const props = defineProps({
  refreshKey: { type: Number, default: 0 },
  focusRequest: { type: Object, default: null },
})
const emit = defineEmits(['error'])

const query = ref('')
const statusFilter = ref('')
const page = ref(1)
const pageSize = 10
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const stats = ref(null)
const statusOptions = ref([])
const detail = ref(null)
const detailLoading = ref(false)

function money(v) {
  return Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function load() {
  loading.value = true
  try {
    const res = await api.listInvoices({
      query: query.value, status: statusFilter.value, page: page.value, page_size: pageSize,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    emit('error', e.friendly)
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    stats.value = await api.stats()
  } catch (e) {
    emit('error', e.friendly)
  }
}

async function openDetail(number) {
  detailLoading.value = true
  try {
    detail.value = await api.getInvoice(number)
  } catch (e) {
    emit('error', e.friendly)
  } finally {
    detailLoading.value = false
  }
}

function search() { page.value = 1; load() }
function reset() { query.value = ''; statusFilter.value = ''; search() }
function goPage(delta) {
  const next = page.value + delta
  if (next < 1 || (next - 1) * pageSize >= total.value) return
  page.value = next
  load()
}

onMounted(async () => {
  await Promise.all([load(), loadStats()])
  try { statusOptions.value = await api.statusOptions() } catch { /* 非关键 */ }
})

watch(() => props.refreshKey, () => { load(); loadStats() })
watch(() => props.focusRequest, (req) => {
  if (!req?.number) return
  query.value = req.number
  page.value = 1
  load()
  openDetail(req.number)
})
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h2><span class="step-num">3</span> 发票台账 <span class="hint">点击任意一行查看行项目、凭证与审计日志</span></h2>
      <button class="btn btn-sm" :disabled="loading" @click="load(); loadStats()">
        <span v-if="loading" class="spin"></span> 刷新数据
      </button>
    </div>
    <div class="card-body">
      <div v-if="stats" class="stat-grid">
        <div class="stat"><div class="label">发票总数</div><div class="value">{{ stats.total_invoices }}</div></div>
        <div class="stat"><div class="label">价税合计</div><div class="value">¥ {{ money(stats.total_amount) }}</div></div>
        <div class="stat"><div class="label">会计凭证</div><div class="value">{{ stats.total_documents }}</div></div>
        <div class="stat"><div class="label">处理会话</div><div class="value">{{ stats.total_sessions }}</div></div>
      </div>

      <div class="filter-row">
        <input v-model="query" class="input" placeholder="搜索发票号 / 销售方 / 购买方 / 文件路径" @keyup.enter="search" />
        <select v-model="statusFilter" class="input" @change="search">
          <option value="">全部状态</option>
          <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
        </select>
        <button class="btn btn-primary" :disabled="loading" @click="search">查询</button>
        <button class="btn btn-sm" :disabled="loading" @click="reset">重置</button>
      </div>

      <div class="table-scroll ledger-table">
        <table class="data">
          <thead>
            <tr>
              <th>发票号</th><th>开票日期</th><th>销售方 → 购买方</th>
              <th>价税合计</th><th>状态</th><th>更新时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.invoice_number" @click="openDetail(r.invoice_number)">
              <td class="mono">{{ r.invoice_number }}</td>
              <td>{{ r.issue_date || '—' }}</td>
              <td class="cell-ellipsis" :title="`${r.seller_name || '—'} → ${r.buyer_name || '—'}`">
                {{ r.seller_name || '—' }} → {{ r.buyer_name || '—' }}
              </td>
              <td class="num">¥ {{ money(r.gross_amount) }}</td>
              <td><StatusBadge :status="r.status" /></td>
              <td>{{ r.updated_at || '—' }}</td>
              <td><span class="row-link" @click.stop="openDetail(r.invoice_number)">查看详情 →</span></td>
            </tr>
            <tr v-if="!rows.length && !loading"><td class="empty" colspan="7">
              暂无发票记录。在上方导入一张发票并完成确认后，数据会出现在这里。
            </td></tr>
          </tbody>
        </table>
      </div>

      <div class="pager">
        <span>共 {{ total }} 条 · 第 {{ page }} 页</span>
        <button class="btn btn-sm" :disabled="page <= 1 || loading" @click="goPage(-1)">上一页</button>
        <button class="btn btn-sm" :disabled="page * pageSize >= total || loading" @click="goPage(1)">下一页</button>
      </div>
      <div v-if="detailLoading" class="muted" style="margin-top:8px">正在加载发票详情…</div>
    </div>
  </div>

  <InvoiceDetail v-if="detail" :detail="detail" @close="detail = null" />
</template>
