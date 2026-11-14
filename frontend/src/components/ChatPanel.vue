<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  session: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  locked: { type: Boolean, default: false },   // 还有一张发票在流程中
  canNext: { type: Boolean, default: false },  // 清单里是否还有待处理发票
})
const emit = defineEmits(['action', 'send'])

const text = ref('')
const scroller = ref(null)
const messages = computed(() => props.session?.messages || [])

const KIND_LABEL = {
  ocr_result: 'OCR 识别结果', quality: '数据质量校验', scenario: '场景匹配',
  reasoning: '推理与校验', approval: '过账审批', result: '过账结果',
  error: '中断 / 错误', system: '系统消息', closed: '人工确认 · 正常结束',
}

// 后端给的按钮文案带 (y)/(e) 等历史快捷键标记，前端换成业务语言，并突出当前主操作。
const ACTION_LABEL = {
  confirm: '确认，继续', edit: '修改字段', skip: '跳过这张',
  approve: '确认过账', reject: '取消过账', restart: '重新识别这张', query: '查询该发票',
  help: '查看可用操作', acknowledge: '确认',
}
const PRIMARY_ACTIONS = new Set(['confirm', 'approve', 'acknowledge'])
// 固定顺序：主操作（确认 / 确认过账）永远在最左，「重新识别这张」永远排最后。
const ACTION_ORDER = {
  acknowledge: 0, confirm: 0, approve: 0, edit: 1, skip: 2,
  reject: 2, query: 3, help: 4, restart: 9,
}
// 业务规则中断（场景未命中）：等人工点「确认」正常结束。
const needsAcknowledge = computed(() =>
  props.session?.status === 'FAILED' && !!props.session?.context?.acknowledge_pending)

const actions = computed(() => {
  const list = (props.session?.available_actions || []).map((a) => ({
    action: a.action, label: a.label, style: a.style,
  }))
  // 历史会话若没带 acknowledge，也补齐「确认」，保证本张能正常结束完成。
  if (needsAcknowledge.value && !list.some((a) => a.action === 'acknowledge')) {
    list.push({ action: 'acknowledge', label: '确认', style: 'primary' })
  }
  return list.sort((a, b) => (ACTION_ORDER[a.action] ?? 5) - (ACTION_ORDER[b.action] ?? 5))
})

// 原来摆在顶部抬头的导航按钮，统一收进「快捷操作」：
// 这样除了「重新识别这张」，用户随时还有其它可点的入口。
const navActions = computed(() => [
  { action: 'folderZone', label: '查看清单', title: '回到上方发票清单', disabled: false },
  { action: 'ledger', label: '查看台账', title: '刷新并跳到发票数据库台账', disabled: false },
  {
    action: 'next', label: '识别下一张 →',
    title: props.locked ? '请先完成当前这张发票的人工确认' : '识别下一张待处理发票，之后仍需逐步人工确认',
    disabled: props.locked || !props.canNext,
  },
])

function actionLabel(a) { return ACTION_LABEL[a.action] || a.label }
function isPrimary(a) { return PRIMARY_ACTIONS.has(a.action) || a.style === 'primary' }
function actionTitle(a) {
  return {
    acknowledge: '确认业务规则中断的结论：本张正常结束完成，不进入本体推理与过账、不生成凭证',
    restart: '重新调用 OCR 识别这一张发票',
  }[a.action] || ''
}

watch(() => messages.value.length, async () => {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
})

function money(v) {
  const n = Number(v || 0)
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function btnClass(style) {
  return { primary: 'btn-primary', danger: 'btn-danger', warn: 'btn-warn' }[style] || ''
}
function send() {
  const value = text.value.trim()
  if (!value || props.busy) return
  emit('send', value)
  text.value = ''
}
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h2><span class="step-num">3</span> 处理记录与人工确认 <span class="hint">识别结果、校验结论、审批与过账都在这里</span></h2>
      <span v-if="session" class="muted" style="font-size:12px">
        {{ messages.length }} 条记录 · {{ session.stage }}
      </span>
    </div>

    <div class="chat-wrap">
      <div ref="scroller" class="chat-scroll">
        <div v-if="!messages.length" class="chat-empty">
          <div class="empty-title">还没有处理记录</div>
          在上方发票清单点「识别这张」，识别结果会显示在这里。<br />
          之后每一步（人工核对、校验、人工确认过账）都会留痕，可随时回看。<br />
          <span style="font-size:12px">也支持直接输入：确认 / 修改 / 跳过 / 确认过账</span>
          <div style="margin-top:14px">
            <button class="btn btn-sm btn-primary" @click="emit('action', 'folderZone')">去选择发票文件夹 →</button>
          </div>
        </div>

        <div v-for="m in messages" :key="m.id" class="msg" :class="[m.role, `kind-${m.kind}`]">
          <div class="msg-avatar">{{ m.role === 'user' ? '我' : m.role === 'system' ? '·' : 'AI' }}</div>
          <div class="msg-body">
            <div class="bubble">
              <div v-if="KIND_LABEL[m.kind] && m.kind !== 'system'" class="msg kind-label">
                {{ KIND_LABEL[m.kind] }}
              </div>
              <div>{{ m.text }}</div>

              <!-- OCR 识别结果 -->
              <template v-if="m.kind === 'ocr_result' && m.data">
                <div class="kv">
                  <div><span class="k">发票号</span><span class="v mono">{{ m.data.invoice?.invoice_number || '缺失' }}</span></div>
                  <div><span class="k">类型</span><span class="v">{{ m.data.invoice?.invoice_type || '—' }}</span></div>
                  <div><span class="k">开票日期</span><span class="v">{{ m.data.invoice?.issue_date || '—' }}</span></div>
                  <div><span class="k">税率</span><span class="v">{{ m.data.invoice?.tax_rate || '—' }}%</span></div>
                  <div><span class="k">销售方</span><span class="v">{{ m.data.invoice?.seller_name || '—' }}</span></div>
                  <div><span class="k">购买方</span><span class="v">{{ m.data.invoice?.buyer_name || '—' }}</span></div>
                  <div><span class="k">价税合计</span><span class="v">¥ {{ money(m.data.invoice?.gross_amount) }}</span></div>
                  <div><span class="k">净额 / 税额</span><span class="v">{{ money(m.data.invoice?.net_amount) }} / {{ money(m.data.invoice?.tax_amount) }}</span></div>
                </div>
                <div class="section-title">行项目（{{ m.data.items?.length || 0 }}）</div>
                <div class="table-scroll">
                  <table class="data">
                    <thead><tr><th>#</th><th>名称</th><th>金额</th><th>税额</th><th>税率</th></tr></thead>
                    <tbody>
                      <tr v-for="it in m.data.items || []" :key="it.item_no">
                        <td>{{ it.item_no }}</td><td>{{ it.description || '—' }}</td>
                        <td class="num">{{ money(it.amount) }}</td>
                        <td class="num">{{ money(it.tax_amount) }}</td><td>{{ it.tax_rate }}%</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-if="m.data.ocr_source" class="muted" style="font-size:11px;margin-top:6px">
                  数据来源：{{ m.data.ocr_source }}
                </div>
              </template>

              <!-- 数据质量 -->
              <template v-else-if="m.kind === 'quality' && m.data?.issues?.length">
                <div v-for="(issue, i) in m.data.issues" :key="i" class="issue-line"
                     :class="issue.level === '阻断' ? 'blocking' : 'warning'">
                  [{{ issue.level }}] {{ issue.rule }} — {{ issue.detail }}
                </div>
              </template>

              <!-- 场景匹配 -->
              <template v-else-if="m.kind === 'scenario' && m.data">
                <div class="kv" style="margin-top:6px">
                  <div class="check-line"><span :class="m.data.checks?.both_companies_matched ? 'ok' : 'no'">
                    {{ m.data.checks?.both_companies_matched ? '✓' : '✗' }}</span> 双方匹配本体公司</div>
                  <div class="check-line"><span :class="m.data.checks?.same_group ? 'ok' : 'no'">
                    {{ m.data.checks?.same_group ? '✓' : '✗' }}</span> 同集团 {{ m.data.checks?.group_id || '' }}</div>
                  <div class="check-line"><span :class="m.data.checks?.special_invoice ? 'ok' : 'no'">
                    {{ m.data.checks?.special_invoice ? '✓' : '✗' }}</span> 增值税专用发票</div>
                  <div class="check-line">开票方 {{ m.data.seller?.code || '?' }} → 收票方 {{ m.data.buyer?.code || '?' }}</div>
                </div>
                <div v-if="m.data.failures?.length" class="notice error" style="margin-top:8px">
                  {{ m.data.failures.join('；') }}
                </div>
              </template>

              <!-- 本体推理 -->
              <template v-else-if="m.kind === 'reasoning' && m.data">
                <div style="margin-top:8px">
                  <span v-for="r in m.data.chain || []" :key="r" class="chip">{{ r }}</span>
                </div>
                <div class="kv" style="margin-top:6px">
                  <div v-for="(v, k) in m.data.checks || {}" :key="k" class="check-line">
                    <span :class="v ? 'ok' : 'no'">{{ v ? '✓' : '✗' }}</span> {{ k }}
                  </div>
                </div>
                <div v-if="m.data.errors?.length" class="notice error" style="margin-top:8px">
                  {{ m.data.errors.join('；') }}
                </div>
              </template>

              <!-- 过账审批 -->
              <template v-else-if="m.kind === 'approval' && m.data">
                <div class="section-title">SHACL 复检</div>
                <div class="kv">
                  <div v-for="(v, k) in m.data.shacl || {}" :key="k" class="check-line">
                    <span :class="v && typeof v === 'boolean' ? 'ok' : 'no'">
                      {{ v && typeof v === 'boolean' ? '✓' : '·' }}</span> {{ k }}：
                    {{ typeof v === 'boolean' ? (v ? '通过' : '未通过') : JSON.stringify(v) }}
                  </div>
                </div>
                <div class="section-title">双主体过账载荷预览</div>
                <div class="table-scroll">
                  <table class="data">
                    <thead><tr><th>主体</th><th>公司代码</th><th>凭证类型</th><th>借方</th><th>贷方</th><th>行数</th></tr></thead>
                    <tbody>
                      <tr><td>A 开票方</td><td>{{ m.data.preview?.company_a?.company_code }}</td>
                        <td>{{ m.data.preview?.company_a?.document_type }}</td>
                        <td class="num">{{ money(m.data.preview?.company_a?.debit) }}</td>
                        <td class="num">{{ money(m.data.preview?.company_a?.credit) }}</td>
                        <td>{{ m.data.preview?.company_a?.line_count }}</td></tr>
                      <tr><td>B 收票方</td><td>{{ m.data.preview?.company_b?.company_code }}</td>
                        <td>{{ m.data.preview?.company_b?.document_type }}</td>
                        <td class="num">{{ money(m.data.preview?.company_b?.debit) }}</td>
                        <td class="num">{{ money(m.data.preview?.company_b?.credit) }}</td>
                        <td>{{ m.data.preview?.company_b?.line_count }}</td></tr>
                    </tbody>
                  </table>
                </div>
                <details class="fold">
                  <summary>查看 BAPI Header / 行项目（{{ m.data.preview?.sap_items?.length || 0 }} 条）</summary>
                  <pre class="json">{{ JSON.stringify({ header: m.data.preview?.sap_header, items: m.data.preview?.sap_items }, null, 2) }}</pre>
                </details>
              </template>

              <!-- 过账结果 -->
              <template v-else-if="m.kind === 'result' && m.data">
                <div class="kv" style="margin-top:6px">
                  <div><span class="k">凭证号</span><span class="v mono">{{ m.data.doc_no || '—' }}</span></div>
                  <div><span class="k">模式</span><span class="v">{{ m.data.mode }}</span></div>
                  <div><span class="k">状态</span><span class="v">{{ m.data.response?.EStatus || '—' }}</span></div>
                  <div><span class="k">消息</span><span class="v">{{ m.data.response?.EMsg || '—' }}</span></div>
                </div>
              </template>
            </div>
            <div class="msg-time">{{ m.ts }}</div>
          </div>
        </div>
      </div>

      <div class="chat-footer">
        <div v-if="needsAcknowledge" class="quick-tip">
          场景未命中已按业务规则中断，不进入本体推理与过账。点最左边的「确认」即可正常结束完成本张
          （不生成会计凭证）；也可以点「重新识别这张」或后面的其它按钮。
        </div>
        <div class="quick-actions">
          <span class="quick-label">快捷操作</span>
          <!-- 业务动作：确认在左、重新识别这张在右 -->
          <button v-for="a in actions" :key="a.action" class="btn"
                  :class="isPrimary(a) ? 'btn-primary' : btnClass(a.style)"
                  :title="actionTitle(a)" :disabled="busy" @click="emit('action', a.action)">
            {{ actionLabel(a) }}
          </button>
          <span class="quick-sep" aria-hidden="true"></span>
          <!-- 导航动作：从抬头移下来的「查看清单 / 查看台账 / 识别下一张」 -->
          <button v-for="n in navActions" :key="n.action" class="btn btn-sm quick-nav"
                  :title="n.title" :disabled="busy || n.disabled" @click="emit('action', n.action)">
            {{ n.label }}
          </button>
        </div>
        <div class="chat-input-row">
          <input v-model="text" class="input" :disabled="!session || busy"
                 placeholder="直接输入指令，例如：确认 / 修改 / 跳过 / 确认过账 / 取消"
                 @keyup.enter="send" />
          <button class="btn btn-primary" :disabled="!session || busy || !text.trim()" @click="send">发送</button>
        </div>
      </div>
    </div>
  </div>
</template>
