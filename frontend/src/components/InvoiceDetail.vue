<script setup>
const props = defineProps({ detail: { type: Object, required: true } })
const emit = defineEmits(['close'])

function money(v) {
  return Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
// 数量 / 单价保留原始精度（最多 4 位小数）；OCR 未提供时显示「—」，不伪装成 0。
function decimal(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}
function short(v, n = 90) {
  const s = String(v ?? '')
  return s.length > n ? `${s.slice(0, n)}…` : s
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal modal-wide">
      <div class="modal-head">
        <h3>发票详情 <span class="mono" style="font-weight:400">{{ detail.invoice_number }}</span></h3>
        <button class="icon-btn" @click="emit('close')">×</button>
      </div>
      <div class="modal-body">
        <div class="kv" style="grid-template-columns:repeat(3,minmax(0,1fr));font-size:13px">
          <div><span class="k">状态</span><span class="v">{{ detail.status }}</span></div>
          <div><span class="k">类型</span><span class="v">{{ detail.invoice_type || '—' }}</span></div>
          <div><span class="k">开票日期</span><span class="v">{{ detail.issue_date || '—' }}</span></div>
          <div><span class="k">销售方</span><span class="v">{{ detail.seller_name || '—' }} ({{ detail.issuer_code || '?' }})</span></div>
          <div><span class="k">购买方</span><span class="v">{{ detail.buyer_name || '—' }} ({{ detail.receiver_code || '?' }})</span></div>
          <div><span class="k">税率</span><span class="v">{{ detail.tax_rate || '—' }}%</span></div>
          <div><span class="k">价税合计</span><span class="v">¥ {{ money(detail.gross_amount) }}</span></div>
          <div><span class="k">净额 / 税额</span><span class="v">{{ money(detail.net_amount) }} / {{ money(detail.tax_amount) }}</span></div>
          <div><span class="k">币种</span><span class="v">{{ detail.currency || '—' }}</span></div>
          <div><span class="k">来源文件</span><span class="v mono">{{ short(detail.source_file, 40) || '—' }}</span></div>
          <div><span class="k">创建时间</span><span class="v">{{ detail.created_at || '—' }}</span></div>
          <div><span class="k">更新时间</span><span class="v">{{ detail.updated_at || '—' }}</span></div>
        </div>

        <div class="section-title">发票行项目</div>
        <div class="table-scroll">
          <table class="data items-table">
            <thead><tr>
              <th>#</th><th>名称</th><th>单位</th><th>数量</th><th>单价</th>
              <th>金额</th><th>税额</th><th>税率</th><th>科目</th><th>成本中心</th>
            </tr></thead>
            <tbody>
              <tr v-for="it in detail.items" :key="it.item_no">
                <td>{{ it.item_no }}</td>
                <td class="desc" :title="it.description || ''">{{ it.description || '—' }}</td>
                <td>{{ it.unit || '—' }}</td>
                <td class="num">{{ decimal(it.quantity) }}</td>
                <td class="num">{{ decimal(it.unit_price) }}</td>
                <td class="num">{{ money(it.amount) }}</td>
                <td class="num">{{ money(it.tax_amount) }}</td>
                <td>{{ it.tax_rate || '—' }}</td>
                <td class="mono">{{ it.account_code || '—' }}</td>
                <td class="mono">{{ it.cost_center_code || '—' }}</td>
              </tr>
              <tr v-if="!detail.items.length"><td class="empty" colspan="10">暂无行项目</td></tr>
            </tbody>
          </table>
        </div>

        <div class="section-title">关联交易事件 / 推理结论</div>
        <div v-if="detail.event" class="notice">
          事件 <span class="mono">{{ detail.event.event_id }}</span> ·
          {{ detail.event.seller_code || '?' }} → {{ detail.event.buyer_code || '?' }} ·
          场景 <strong>{{ detail.event.scenario_id || '—' }}</strong> ·
          状态 {{ detail.event.status }} · 金额 ¥ {{ money(detail.event.amount) }}
          <template v-if="detail.reasoning_payload?.validation">
            <br />SHACL / 校验：
            <span v-for="(v, k) in detail.reasoning_payload.validation" :key="k">
              <span v-if="typeof v === 'boolean'">{{ k }}={{ v ? '✓' : '✗' }}；</span>
            </span>
            <br />规则链：{{ (detail.reasoning_payload.audit_trail?.reasoning_chain || []).join(' → ') || '—' }}
          </template>
        </div>
        <div v-else class="notice">暂无关联交易事件（发票可能尚未进入场景匹配阶段）。</div>

        <div class="section-title">会计凭证（双主体）</div>
        <div class="table-scroll">
          <table class="data">
            <thead><tr><th>凭证号</th><th>公司代码</th><th>类型</th><th>借方</th><th>贷方</th><th>状态</th><th>派生</th></tr></thead>
            <tbody>
              <tr v-for="(d, i) in detail.documents" :key="i">
                <td class="mono">{{ d.doc_no }}</td><td>{{ d.company_code }}</td><td>{{ d.doc_type || '—' }}</td>
                <td class="num">{{ money(d.total_debit) }}</td><td class="num">{{ money(d.total_credit) }}</td>
                <td>{{ d.status }}</td><td class="mono">{{ d.derived_doc || '—' }}</td>
              </tr>
              <tr v-if="!detail.documents.length"><td class="empty" colspan="7">尚未生成凭证</td></tr>
            </tbody>
          </table>
        </div>

        <div v-if="detail.posting_events.length" class="section-title">过账事件</div>
        <div v-for="p in detail.posting_events" :key="p.posting_id" class="notice" style="margin-bottom:6px">
          <span class="mono">{{ p.posting_id }}</span> · 凭证 {{ p.doc_ref }} ·
          {{ p.company_code }} · EStatus={{ p.bapi_status || '—' }} · {{ short(p.message, 70) || '—' }}
        </div>

        <div v-if="detail.quality_issues.length" class="section-title">数据质量记录</div>
        <div v-for="(q, i) in detail.quality_issues" :key="i" class="issue-line"
             :class="q.level === '阻断' ? 'blocking' : 'warning'">
          [{{ q.level }}] {{ q.rule }} — {{ q.detail }}
        </div>

        <div class="section-title">审计日志（最近）</div>
        <div v-for="a in detail.audit_logs.slice(0, 8)" :key="a.id" class="issue-line">
          <span class="mono">{{ a.created_at }}</span> · <strong>{{ a.action }}</strong> · {{ short(a.detail, 110) }}
        </div>

        <details v-if="detail.reasoning_payload" class="fold">
          <summary>查看完整 JSON-LD 过账载荷</summary>
          <pre class="json">{{ JSON.stringify(detail.reasoning_payload, null, 2) }}</pre>
        </details>
      </div>
      <div class="modal-foot"><button class="btn" @click="emit('close')">关闭</button></div>
    </div>
  </div>
</template>
