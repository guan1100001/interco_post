<script setup>
import { reactive } from 'vue'

const props = defineProps({ draft: { type: Object, required: true } })
const emit = defineEmits(['submit', 'close'])

const HEADER = [
  ['invoice_number', '发票号码'], ['invoice_type', '发票类型'],
  ['issue_date', '开票日期'], ['tax_rate', '税率(%)'],
  ['seller_name', '销售方名称'], ['buyer_name', '购买方名称'],
  ['seller_tax_id', '销售方税号'], ['buyer_tax_id', '购买方税号'],
  ['gross_amount', '价税合计'], ['net_amount', '不含税金额'],
  ['tax_amount', '税额'], ['currency', '币种'],
  ['remark', '备注'], ['invoice_issuer', '开票人'],
]
const NUMERIC = new Set([
  'gross_amount', 'net_amount', 'tax_amount',
  'amount', 'tax_amount', 'quantity', 'unit_price',
])
const ITEM_COLS = [
  ['description', '名称'], ['amount', '金额'], ['tax_amount', '税额'],
  ['tax_rate', '税率'], ['quantity', '数量'], ['unit', '单位'], ['unit_price', '单价'],
]

const form = reactive({
  invoice: { ...(props.draft?.invoice || {}) },
  items: (props.draft?.items || []).map((it) => ({ ...it })),
})

// 数值字段留空时显式提交 null：后端会写成 SQL NULL，而不是把空字符串塞进 REAL 列。
function normalized(key, value) {
  if (NUMERIC.has(key) && (value === '' || value === null || value === undefined)) return null
  return value ?? ''
}

function submit() {
  const invoice = {}
  HEADER.forEach(([key]) => { invoice[key] = normalized(key, form.invoice[key]) })
  const items = form.items.map((it) => {
    const row = { item_no: it.item_no }
    ITEM_COLS.forEach(([key]) => { row[key] = normalized(key, it[key]) })
    return row
  })
  emit('submit', { invoice, items })
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <h3>修改发票识别结果 <span class="muted" style="font-size:12px;font-weight:400">对应原流程的 e（修改）</span></h3>
        <button class="icon-btn" @click="emit('close')">×</button>
      </div>
      <div class="modal-body">
        <div class="notice" style="margin-bottom:14px">
          留空或保持原值均可；提交后后端会重新执行数据质量校验，阻断项消失后才能确认入库。
        </div>
        <div class="section-title">发票抬头</div>
        <div class="form-grid">
          <label v-for="[key, label] in HEADER" :key="key" class="field">
            <span>{{ label }}</span>
            <input v-model="form.invoice[key]" class="input"
                   :type="NUMERIC.has(key) ? 'number' : 'text'"
                   :step="NUMERIC.has(key) ? '0.01' : undefined" />
          </label>
        </div>

        <div class="section-title">行项目</div>
        <div class="table-scroll">
          <table class="data">
            <thead><tr><th>#</th><th v-for="[key, label] in ITEM_COLS" :key="key">{{ label }}</th></tr></thead>
            <tbody>
              <tr v-for="it in form.items" :key="it.item_no">
                <td>{{ it.item_no }}</td>
                <td v-for="[key] in ITEM_COLS" :key="key">
                  <input v-model="it[key]" class="input" style="min-width:96px;padding:4px 7px"
                         :type="['amount','tax_amount','quantity','unit_price'].includes(key) ? 'number' : 'text'"
                         step="0.01" />
                </td>
              </tr>
              <tr v-if="!form.items.length"><td class="empty" :colspan="ITEM_COLS.length + 1">暂无行项目</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn btn-primary" @click="submit">提交修改并重新校验</button>
      </div>
    </div>
  </div>
</template>
