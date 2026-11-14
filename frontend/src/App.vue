<script setup>
/**
 * 单页业务操作台：登录后才进入，所有功能在同一页面完成，不做导航、不做视图切换。
 *
 * 业务流程（无批量）：选文件夹 → 扫描清单 → 人工点选一张 → 识别
 *   → 人工核对确认 → 场景匹配/推理校验 → 人工确认过账 → 人工点「识别下一张」。
 * 约束：同一时刻只允许一张发票处于流程中；不存在后台自动连续处理、停止按钮或进度轮询。
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from './api/endpoints'
import { clearAccessToken, onUnauthorized } from './api/client'
import FolderPanel from './components/FolderPanel.vue'
import ImportPanel from './components/ImportPanel.vue'
import ChatPanel from './components/ChatPanel.vue'
import QueryPanel from './components/QueryPanel.vue'
import InvoiceEditForm from './components/InvoiceEditForm.vue'
import LoginView from './components/LoginView.vue'
import AccountPanel from './components/AccountPanel.vue'

const FLOW = [
  { label: '选文件夹' },
  { label: '识别一张发票' },
  { label: '人工核对确认' },
  { label: '推理与校验' },
  { label: '人工确认过账' },
  { label: '手动处理下一张' },
]
const ROLE_LABEL = { admin: '管理员', operator: '操作员', viewer: '只读' }
// ACKNOWLEDGED：业务规则中断（如场景未命中）经人工点「确认」后的正常结束态，未过账。
const TERMINAL = ['COMPLETED', 'ACKNOWLEDGED', 'FAILED', 'SKIPPED', 'CANCELLED']

// 共享表单状态：App 与子组件直接修改对象属性（响应式）。
const form = reactive({ folder: '', path: '', ocrMode: 'auto', sapMode: 'mock', recursive: false })

const meta = ref(null)
const online = ref(false)
const session = ref(null)
const busy = ref(false)
const sapBusy = ref(false)          // 过账模式切换请求进行中
const toast = ref(null)
const editorOpen = ref(false)
const refreshKey = ref(0)
const focusRequest = ref(null)
// 认证状态
const user = ref(null)
const authReady = ref(false)
const accountOpen = ref(false)
const loginNotice = ref('')
// 文件夹清单与「一次一张」状态
const scanResult = ref(null)
const scanBusy = ref(false)
const activeSessionId = ref(null)       // 当前仍在流转的会话：非空时不允许开新的一张
const lastProcessedPath = ref('')        // 避免「识别下一张」重复挑到刚结束的那张
const results = reactive({})             // 规范化路径 -> { status, sessionId, invoiceNumber, docNo, message }
let toastTimer = null
let pollTimer = null

watch(() => meta.value?.ocr_mode, (v) => { if (v) form.ocrMode = v })
// 后端 /meta 返回的是「当前生效」的过账模式（可能已被界面在运行时改过）。
watch(() => meta.value?.sap_mode, (v) => { if (v) form.sapMode = v }, { immediate: true })

function norm(p) {
  return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}
function shortPath(p) {
  const parts = String(p || '').split(/[\\/]/)
  return parts[parts.length - 1] || p
}
function notify(message, type = 'info') {
  toast.value = { message, type }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = null }, 4200)
}
function fail(message) { if (message) notify(message, 'error') }
function onAccountError(message) { fail(message) }
function scrollTo(id) {
  nextTick(() => {
    const el = document.getElementById(id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

// ---------------------------------------------------------------- 认证门控
async function loadMeta() {
  try {
    meta.value = await api.meta()
    online.value = true
  } catch (e) {
    online.value = false
    if (user.value) fail(e.friendly)   // 登录页不弹出后端错误提示
  }
}

/**
 * 切换过账模式（mock / dry_run / live）。以后端返回为准刷新 meta，
 * 顶部状态、审批提示、实际过账分支都依赖 meta.sap_mode，必须保持一致。
 * 失败时把下拉回滚，避免「界面显示 live、后端仍是 mock」的危险错觉。
 */
async function changeSapMode(mode) {
  const previous = form.sapMode
  sapBusy.value = true
  try {
    const res = await api.setSapMode(mode)
    form.sapMode = res.sap_mode
    meta.value = {
      ...(meta.value || {}),
      sap_mode: res.sap_mode,
      sap_mode_options: res.sap_mode_options,
      sap_live_ready: res.sap_live_ready,
      sap_missing_config: res.sap_missing_config,
      sap_mode_runtime_override: res.sap_mode_runtime_override,
    }
    notify(
      res.sap_mode === 'live' ? '过账模式已切换为 live（真实 SAP），请留意下方配置提示' : `过账模式已切换为 ${res.sap_mode}`,
      'success',
    )
  } catch (e) {
    form.sapMode = previous
    fail(e.friendly)
  } finally {
    sapBusy.value = false
  }
}

async function loadDefaultScan() {
  // 打开页面就列出默认发票目录，用户不必先猜路径
  try {
    const res = await api.scanFolder('', form.recursive)
    if (!res.total) return
    scanResult.value = res
    if (!form.folder) form.folder = res.directory
    const first = (res.files || []).find((f) => !f.processed)
    if (first && !form.path.trim()) form.path = first.path
  } catch { /* 目录不存在时保留手动输入 */ }
}

async function loadSessionHistory() {
  // 刷新页面后恢复清单里每张的最近处理记录，并接回上一次「还没确认完」的会话
  try {
    const list = await api.listSessions(50)   // 最新在前
    for (const s of list) {
      const key = norm(s.file_path)
      if (!results[key]) {
        results[key] = {
          status: s.status, sessionId: s.id, invoiceNumber: s.invoice_number || '',
          docNo: '', acknowledgePending: false,
          message: TERMINAL.includes(s.status)
            ? '已有处理记录，可点「记录」查看'
            : '上次流程未完成，可重新识别',
        }
      }
    }
    // 只接回「最近一次」仍未完成的会话：用户刚才刷新页面时正在确认的那张。
    // 更早的 WAITING 会话视为已放弃，不在每次打开页面时强行恢复、锁住操作台。
    const pending = list.length && !TERMINAL.includes(list[0].status) ? list[0] : null
    if (pending) {
      const snap = await api.getSession(pending.id)
      session.value = snap
      form.path = snap.file_path
      lastProcessedPath.value = norm(snap.file_path)
      syncResult(snap)
      notify(`已恢复上次未完成的发票：${shortPath(snap.file_path)}，请继续人工确认`, 'info')
    }
  } catch { /* 历史记录读取失败不影响主流程 */ }
}

async function bootstrapAuth() {
  try {
    const me = await api.me()          // 有有效刷新 Cookie 时客户端会自动换新访问令牌
    user.value = me
    await loadMeta()
    await loadDefaultScan()
    await loadSessionHistory()
  } catch {
    user.value = null
    online.value = false
  } finally {
    authReady.value = true
  }
}

async function onLoginSuccess(u) {
  user.value = u
  loginNotice.value = ''
  await loadMeta()
  await loadDefaultScan()
  await loadSessionHistory()
  notify(`欢迎回来，${u.display_name || u.username}`, 'success')
}

function resetWorkbench() {
  session.value = null
  scanResult.value = null
  meta.value = null
  online.value = false
  activeSessionId.value = null
  lastProcessedPath.value = ''
  for (const k of Object.keys(results)) delete results[k]
  form.folder = ''
  form.path = ''
  editorOpen.value = false
  accountOpen.value = false
}

async function doLogout(message = '') {
  try { await api.logout() } catch { /* 网络异常也要清理本地登录态 */ }
  clearAccessToken()
  user.value = null
  resetWorkbench()
  loginNotice.value = message
}

function onPasswordChanged() {
  accountOpen.value = false
  doLogout('密码已修改，请使用新密码重新登录')
}

// ------------------------------------------------------- 文件夹清单（只读）
async function doScan() {
  scanBusy.value = true
  try {
    const res = await api.scanFolder(form.folder.trim(), form.recursive)
    scanResult.value = res
    form.folder = res.directory
    const stillThere = (res.files || []).some((f) => norm(f.path) === norm(form.path))
    if (!stillThere) {
      const first = (res.files || []).find((f) => !f.processed)
      form.path = first ? first.path : ''
    }
    notify(res.message, 'success')
    scrollTo('folder-zone')
  } catch (e) {
    fail(e.friendly)
  } finally {
    scanBusy.value = false
  }
}

/** App 合并扫描结果 + 本次/历史会话状态，供清单渲染。 */
const queueRows = computed(() => {
  const activeKey = activeSessionId.value ? norm(session.value?.file_path) : ''
  return (scanResult.value?.files || []).map((f, i) => {
    const key = norm(f.path)
    const r = results[key] || {}
    const isActive = !!activeKey && key === activeKey && session.value?.id === activeSessionId.value
    let status = 'PENDING'
    let label = '待处理'
    let message = '等待人工选择处理'

    if (isActive) {
      status = 'ACTIVE'
      label = session.value?.stage === 'AWAIT_APPROVAL'
        ? '等待确认过账'
        : session.value?.status === 'WAITING' ? '等待核对确认' : '识别处理中'
      message = session.value?.stage === 'AWAIT_APPROVAL'
        ? '校验已通过，等待人工确认过账'
        : '识别完成，等待人工核对并确认'
    } else if (r.status === 'COMPLETED') {
      status = 'COMPLETED'; label = '已过账'
      message = r.docNo ? `过账完成 · 凭证 ${r.docNo}` : '过账完成'
    } else if (r.status === 'ACKNOWLEDGED') {
      status = 'ACKNOWLEDGED'; label = '已确认完成'
      message = '场景未命中已人工确认，按业务规则正常结束，未过账'
    } else if (r.status === 'FAILED') {
      // 业务规则中断不是技术失败：清单提示用户点「记录」进去确认结束
      const needAck = r.acknowledgePending || f.acknowledge_pending
      status = needAck ? 'AWAIT_ACK' : 'FAILED'
      label = needAck ? '待确认结束' : '处理失败'
      message = needAck
        ? '场景未命中，按业务规则中断；点「记录」确认结束，也可以重新识别'
        : (r.message || '识别或过账失败，可重新识别')
    } else if (r.status === 'SKIPPED') {
      status = 'SKIPPED'; label = '已跳过'
      message = '已跳过，未写入数据库，可重新识别'
    } else if (r.status === 'CANCELLED') {
      status = 'CANCELLED'; label = '已取消'
      message = '已取消过账，未生成凭证，可重新识别'
    } else if (f.processed) {
      status = 'COMPLETED'; label = '已过账'
      message = '数据库已有过账记录，避免重复处理'
    } else if (r.sessionId) {
      message = r.message || '有历史处理记录，可重新识别'
    }

    return {
      index: i + 1, key, name: f.name, path: f.path,
      status, label, message, isActive,
      done: status === 'COMPLETED' || status === 'ACKNOWLEDGED',
      invoiceNumber: r.invoiceNumber || f.invoice_number || '',
      docNo: r.docNo || f.doc_no || '',
      sessionId: r.sessionId || f.last_session_id || null,
    }
  })
})

const stats = computed(() => {
  const rows = queueRows.value
  return {
    total: rows.length,
    done: rows.filter((r) => r.done).length,
    posted: rows.filter((r) => r.status === 'COMPLETED').length,
    closed: rows.filter((r) => r.status === 'ACKNOWLEDGED').length,
    pending: rows.filter((r) => !r.done && !r.isActive).length,
  }
})
const selectedKey = computed(() => norm(form.path))
const locked = computed(() => !!activeSessionId.value)

// 只拦「已过账」的发票（重复过账会造成重复凭证）。
// 场景未命中确认结束 / 跳过 / 取消 / 识别失败 都允许人工再点「识别这一张发票」重新识别。
function isPostedFile(filePath) {
  const key = norm(filePath)
  if (results[key]?.status === 'COMPLETED') return true
  return !!(scanResult.value?.files || []).find((f) => norm(f.path) === key && f.processed)
}

// ------------------------------------------------------- 一次只处理一张发票
function syncResult(snap) {
  const key = norm(snap.file_path)
  const prev = results[key] || {}
  const posting = snap.context?.posting || {}
  const label = {
    COMPLETED: '过账完成', FAILED: '流程中断',
    ACKNOWLEDGED: '已确认结束（未过账）',
    SKIPPED: '已跳过，未写入数据库', CANCELLED: '已取消过账',
  }
  results[key] = {
    status: snap.status,
    sessionId: snap.id,
    invoiceNumber: snap.invoice_number || prev.invoiceNumber || '',
    docNo: posting.doc_no || prev.docNo || '',
    acknowledgePending: !!snap.context?.acknowledge_pending,
    message: TERMINAL.includes(snap.status)
      ? (label[snap.status] || snap.status) + (posting.doc_no ? ` · 凭证 ${posting.doc_no}` : '')
      : snap.stage === 'AWAIT_APPROVAL' ? '等待人工确认过账' : '等待人工核对确认',
  }
  activeSessionId.value = TERMINAL.includes(snap.status) ? null : snap.id
}

/**
 * 识别一张发票：唯一入口，真正调用后端 POST /pipeline/sessions（后端立即执行 OCR）。
 * @param {string} filePath 发票文件完整路径（不是对象）
 * @param {{ocrMode?: string, force?: boolean}} opts
 *   force=true 表示用户显式点了「识别这一张发票」重新识别，允许替换当前未完成的会话。
 */
async function startOne(filePath, opts = {}) {
  // 兼容历史上子组件传 {file_path, ocr_mode} 的写法，避免再把对象当路径发给后端。
  if (filePath && typeof filePath === 'object') {
    const payload = filePath
    opts = { ...payload, ...(opts || {}) }
    filePath = payload.file_path
  }
  const path = typeof filePath === 'string' ? filePath.trim() : ''
  const ocrMode = opts.ocrMode || opts.ocr_mode || form.ocrMode
  if (!path) { fail('请先选择一张发票'); return }
  if (activeSessionId.value && !opts.force) {
    fail('当前还有一张发票未完成，请先人工完成确认（或跳过 / 取消过账）再处理下一张')
    return
  }
  if (isPostedFile(path)) {
    fail('该发票已过账，不能重复处理；请在清单里选择待处理的发票')
    return
  }
  busy.value = true
  try {
    form.path = path
    form.ocrMode = ocrMode
    lastProcessedPath.value = norm(path)
    const snap = await api.createSession(path, ocrMode)
    session.value = snap
    syncResult(snap)
    refreshKey.value++
    notify(
      snap.status === 'FAILED'
        ? '识别失败，请查看右侧处理记录'
        : `已调用 OCR 识别 ${shortPath(path)}（${ocrMode}），请核对后人工确认`,
      snap.status === 'FAILED' ? 'error' : 'success',
    )
    scrollTo('chat-zone')
  } catch (e) {
    fail(e.friendly)
  } finally {
    busy.value = false
  }
}

async function startNext() {
  if (activeSessionId.value) { fail('请先完成当前这张发票的人工确认'); return }
  const next = queueRows.value.find(
    (r) => !r.done && !r.isActive && r.key !== lastProcessedPath.value,
  )
  if (!next) {
    notify('文件夹里没有其它待处理的发票了', 'success')
    scrollTo('folder-zone')
    return
  }
  await startOne(next.path)
}

function selectRow(row) {
  if (row.done) {
    notify('该发票已过账，不能重复处理；请选择「待处理」的发票', 'info')
    return
  }
  form.path = row.path
  notify(`已选中 ${row.name}，点「识别这一张发票」开始`, 'info')
  scrollTo('import-zone')
}

async function openSession(id) {
  if (activeSessionId.value) {
    fail('当前还有一张发票待人工确认，请先完成它再查看其它记录')
    return
  }
  try {
    session.value = await api.getSession(id)
    form.path = session.value.file_path
    scrollTo('chat-zone')
  } catch (e) {
    fail(e.friendly)
  }
}

// ---------------------------------------------------------------- 当前任务
const task = computed(() => {
  const s = session.value
  const disabled = busy.value || scanBusy.value
  const pending = stats.value.pending

  if (!s) {
    if (!queueRows.value.length) {
      return {
        badge: '待开始', tone: 'idle', step: 0,
        title: '先选择发票文件夹',
        desc: '填写文件夹路径并点「扫描文件夹」列出可识别发票；之后一次只处理一张，识别结果与过账都要人工确认。',
        primary: { label: '扫描文件夹', action: 'scan', disabled },
        secondary: [{ label: '查看清单', action: 'folderZone' }],
      }
    }
    return {
      badge: '待开始', tone: 'idle', step: 1,
      title: `清单已就绪 · ${pending} 张待处理`,
      desc: form.path.trim()
        ? `当前选中 ${shortPath(form.path)}。点右侧按钮只识别这一张；识别结果需要人工核对确认，过账前还要再确认一次。`
        : '在清单里点一行选中一张发票，再点「识别这一张发票」。系统不会自动连续处理。',
      primary: !pending
        ? { label: '换文件夹重新扫描', action: 'scan', disabled }
        : {
          label: form.path.trim() ? '识别这一张发票' : '识别下一张待处理',
          action: form.path.trim() ? 'start' : 'next',
          disabled: disabled || locked.value,
        },
      secondary: [
        { label: '重新扫描文件夹', action: 'scan' },
        { label: '查看台账', action: 'ledger' },
      ],
    }
  }

  const num = s.invoice_number || '待确认'
  const stage = s.stage

  if (s.status === 'COMPLETED') {
    const doc = s.context?.posting?.doc_no
    return {
      badge: '已完成', tone: 'success', step: 5,
      title: '这张发票已人工确认并过账',
      desc: `发票 ${num}${doc ? ` · 凭证 ${doc}` : ''}。记录已写入台账。`
        + (pending ? `文件夹里还有 ${pending} 张待处理，需要你手动开始下一张。` : '文件夹里的发票都处理完了。'),
      primary: pending
        ? { label: '识别下一张 →', action: 'next', disabled }
        : { label: '查看发票清单', action: 'folderZone', disabled: false },
      secondary: [
        { label: '查看发票清单', action: 'folderZone' },
        { label: '查看台账', action: 'ledger' },
        { label: '查询该发票', action: 'query' },
      ],
    }
  }

  // 业务规则中断经人工确认后：正常结束、未过账，可手动开始下一张。
  if (s.status === 'ACKNOWLEDGED') {
    return {
      badge: '已完成', tone: 'success', step: 5,
      title: '已人工确认结束 · 未过账',
      desc: '场景未命中已按业务规则中断并人工确认，本张正常结束，未进入本体推理与过账，未生成凭证。'
        + (pending ? `文件夹里还有 ${pending} 张待处理，可手动开始下一张。` : '文件夹里的发票都处理完了。'),
      primary: pending
        ? { label: '识别下一张 →', action: 'next', disabled }
        : { label: '查看发票清单', action: 'folderZone', disabled: false },
      secondary: [
        { label: '查看发票清单', action: 'folderZone' },
        { label: '查看台账', action: 'ledger' },
      ],
    }
  }

  if (TERMINAL.includes(s.status)) {
    // 场景未命中等待人工收尾：给「确认」正常结束，也保留「重新识别这张」。
    if (s.status === 'FAILED' && s.context?.acknowledge_pending) {
      return {
        badge: '待人工确认', tone: 'muted', step: 5,
        title: '场景未命中 · 已按业务规则中断',
        desc: '不进入本体推理与过账。在右侧「快捷操作」点「确认」正常结束本张，也可以点「重新识别这张」重新识别。',
        primary: { label: '确认，正常结束', action: 'acknowledge', disabled },
        secondary: [
          { label: '重新识别这张', action: 'restart' },
          { label: '查看清单', action: 'folderZone' },
          { label: '查看台账', action: 'ledger' },
        ],
      }
    }
    const reason = {
      SKIPPED: '已跳过，未写入数据库',
      CANCELLED: '已取消过账，未生成凭证',
      FAILED: '流程中断，请查看右侧记录',
    }[s.status] || '流程已结束'
    return {
      badge: '已结束', tone: 'muted', step: 5,
      title: '本次流程已结束',
      desc: `${reason}。可以重新识别这张，或手动开始下一张（还有 ${pending} 张待处理）。`,
      primary: { label: '重新识别这张', action: 'restart', disabled },
      secondary: [
        { label: '识别下一张', action: 'next' },
        { label: '查看清单', action: 'folderZone' },
        { label: '查看台账', action: 'ledger' },
      ],
    }
  }

  if (stage === 'AWAIT_OCR_CONFIRM' && s.status === 'WAITING') {
    return {
      badge: '等待你确认', tone: 'active', step: 2,
      title: `人工核对识别结果 · ${num}`,
      desc: '请对照右侧识别结果核对发票号、购销双方、金额与行项目。确认后才进行场景匹配、本体推理与 SHACL 校验。',
      primary: { label: '确认，继续', action: 'confirm', disabled },
      secondary: [
        { label: '修改字段', action: 'edit' },
        { label: '跳过这张', action: 'skip', style: 'danger' },
        { label: '查看清单', action: 'folderZone' },
      ],
    }
  }

  if (stage === 'AWAIT_APPROVAL' && s.status === 'WAITING') {
    const modeDesc = {
      mock: '当前为模拟过账（mock）：会生成模拟凭证并写入本地数据库，不连接真实 SAP。',
      dry_run: '当前为预览（dry_run）：只校验与预览载荷，不写入凭证、不调用 SAP。',
      live: '当前为真实 SAP（live）：确认过账将调用 ZfmAico002 执行双主体同步过账，不可撤销。',
    }[meta.value?.sap_mode] || '过账模式读取中…'
    return {
      badge: '等待过账确认', tone: 'active', step: 4,
      title: `人工确认过账 · ${num}`,
      desc: `校验已通过，请核对右侧双主体过账载荷，确认后才执行过账。${modeDesc}`
        + '可在左侧「过账模式」随时切换，实际以点「确认过账」时的设置为准。',
      primary: { label: '确认过账', action: 'approve', disabled },
      secondary: [
        { label: '取消过账', action: 'reject', style: 'danger' },
        { label: '查看清单', action: 'folderZone' },
      ],
    }
  }

  if (s.status === 'RUNNING') {
    return {
      badge: '处理中', tone: 'active', step: stage === 'INIT' ? 1 : 2,
      title: '正在识别，请稍候…',
      desc: `发票 ${num} · 当前阶段 ${stage}。`,
      primary: { label: '处理中…', action: 'noop', disabled: true },
      secondary: [],
    }
  }

  return {
    badge: stage, tone: 'active', step: 3,
    title: `流程进行中 · ${num}`,
    desc: '可继续在右侧对话区人工输入指令，或使用下方快捷按钮。',
    primary: { label: '查看处理记录', action: 'chatZone', disabled },
    secondary: [{ label: '查看台账', action: 'ledger' }],
  }
})

function flowClass(i) {
  const t = task.value.step
  if (t < 0) return ''
  if (i < t || (t === 5 && i === 5)) return 'done'
  if (i === t) return 'current'
  return ''
}

// ---------------------------------------------------------------- 业务动作
async function sendMessage(payload, successMessage) {
  if (!session.value) return
  busy.value = true
  try {
    const prevStatus = session.value.status
    const snap = await api.sendMessage(session.value.id, payload)
    session.value = snap
    syncResult(snap)
    const justFinished = TERMINAL.includes(snap.status) && prevStatus !== snap.status
    if (justFinished) {
      refreshKey.value++
      notify(
        snap.status === 'COMPLETED'
          ? '过账完成，记录已写入台账。请手动点「识别下一张」'
          : snap.status === 'ACKNOWLEDGED'
            ? '已人工确认，本张按业务规则正常结束（未过账）。请手动点「识别下一张」'
            : '流程已结束，可在清单里手动选择下一张',
        'success',
      )
    } else if (successMessage) {
      notify(successMessage, 'success')
    }
  } catch (e) {
    fail(e.friendly)
  } finally {
    busy.value = false
  }
}

async function onAction(action) {
  if (action === 'scan') { await doScan(); return }
  if (action === 'next') { await startNext(); return }
  if (action === 'folderZone') { scrollTo('folder-zone'); return }
  if (action === 'chatZone') { scrollTo('chat-zone'); return }
  if (action === 'start') { await startOne(form.path.trim(), { force: true }); return }
  if (action === 'edit') { editorOpen.value = true; return }
  if (action === 'focusImport') { scrollTo('import-zone'); return }
  if (action === 'ledger') { refreshKey.value++; scrollTo('ledger-zone'); return }
  if (action === 'query') {
    focusRequest.value = { number: session.value?.invoice_number || '', seq: Date.now() }
    refreshKey.value++
    scrollTo('ledger-zone')
    return
  }
  await sendMessage({ action })
}

function onSend(text) { sendMessage({ text }) }
function submitEdit(patch) {
  editorOpen.value = false
  sendMessage({ action: 'edit', patch, text: '提交了字段修改' })
}

async function poll() {
  // 只跟随「当前正在流转」的会话，避免查看历史记录时误改状态
  if (!user.value || busy.value) return
  if (!activeSessionId.value || session.value?.id !== activeSessionId.value) return
  try {
    const snap = await api.getSession(activeSessionId.value)
    session.value = snap
    syncResult(snap)
  } catch { /* 静默 */ }
}

onMounted(() => {
  onUnauthorized(() => {
    clearAccessToken()
    user.value = null
    resetWorkbench()
  })
  bootstrapAuth()
  pollTimer = setInterval(poll, 4000)
})
onUnmounted(() => { clearInterval(pollTimer); clearTimeout(toastTimer) })
</script>

<template>
  <div v-if="!authReady" class="auth-boot"><span class="spin"></span> 正在验证登录状态…</div>

  <LoginView v-else-if="!user" :notice="loginNotice" @success="onLoginSuccess" />

  <div v-else class="workbench">
    <!-- 顶栏：身份、运行状态与当前登录账号 -->
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M5 4h11l3 3v13H5z" stroke="#fff" stroke-width="1.8" stroke-linejoin="round" />
              <path d="M8 10h8M8 14h8M8 18h5" stroke="#fff" stroke-width="1.6" stroke-linecap="round" />
            </svg>
          </div>
          <div>
            <div class="brand-name">关联交易发票处理台</div>
            <div class="brand-sub">选文件夹 · 逐张识别 · 人工确认 · 过账 · 台账</div>
          </div>
        </div>
        <div class="topbar-right">
          <span class="conn" :class="online ? 'ok' : 'down'"><i></i>{{ online ? '服务已连接' : '服务未连接' }}</span>
          <span v-if="meta" class="env-chip">OCR {{ meta.ocr_mode }}</span>
          <span v-if="meta" class="env-chip" :class="meta.sap_mode === 'live' ? 'live' : 'sim'"
                :title="meta.sap_mode === 'live'
                  ? '真实 SAP 过账：确认过账将调用 ZfmAico002 执行双主体同步过账'
                  : (meta.sap_mode === 'dry_run'
                    ? '预览模式：只校验与预览载荷，不写入凭证'
                    : '模拟模式：凭证仅写入本地数据库，不会进入真实 SAP')">
            SAP {{ meta.sap_mode }}<em>{{ meta.sap_mode === 'live' ? '真实' : '模拟' }}</em>
          </span>
          <a class="topbar-link" href="/docs" target="_blank" rel="noopener">API 文档 ↗</a>
          <span class="topbar-sep"></span>
          <div class="user-box" :title="`账号 ${user.username} · 角色 ${ROLE_LABEL[user.role] || user.role}`">
            <span class="user-avatar">{{ (user.display_name || user.username).slice(0, 1).toUpperCase() }}</span>
            <span class="user-meta">
              <strong>{{ user.display_name || user.username }}</strong>
              <em>{{ ROLE_LABEL[user.role] || user.role }}</em>
            </span>
          </div>
          <button class="topbar-btn" @click="accountOpen = true">账号设置</button>
          <button class="topbar-btn" @click="doLogout('')">退出登录</button>
        </div>
      </div>
    </header>

    <main class="page">
      <!-- 当前任务：把「下一步该做什么」和「点哪里」直接摆在最显眼的位置 -->
      <section class="taskbar" :class="`tone-${task.tone}`">
        <div class="taskbar-top">
          <div class="taskbar-info">
            <div class="eyebrow">当前任务 <span class="task-badge">{{ task.badge }}</span></div>
            <h2 class="taskbar-title">{{ task.title }}</h2>
            <p class="taskbar-desc">{{ task.desc }}</p>
          </div>
          <!-- 抬头只做状态与引导：所有操作按钮统一收在右下角「快捷操作」，不在这里重复摆放 -->
          <div class="taskbar-guide">
            <span class="guide-badge">操作入口</span>
            <span>所有按钮都在右侧下方「快捷操作」</span>
            <template v-if="task.primary">
              <span class="guide-sep">·</span>
              <span>下一步：<strong>{{ task.primary.label }}</strong></span>
            </template>
          </div>
        </div>
        <div class="flow">
          <div v-for="(f, i) in FLOW" :key="f.label" class="flow-step" :class="flowClass(i)">
            <span class="flow-dot">{{ flowClass(i) === 'done' ? '✓' : i + 1 }}</span>
            <span class="flow-label">{{ f.label }}</span>
          </div>
        </div>
      </section>

      <!-- 选文件夹 → 逐张清单，与所有功能同页 -->
      <FolderPanel :form="form" :scan="scanResult" :rows="queueRows" :stats="stats"
                   :selected-key="selectedKey" :locked="locked" :busy="busy" :scan-busy="scanBusy"
                   @scan="doScan" @select="selectRow" @start="startOne" @next="startNext"
                   @open-session="openSession" />

      <!-- 工作区：左边当前发票与设置，右边人工确认对话，同屏可见 -->
      <div class="work-grid">
        <div id="import-zone" class="col-left">
          <ImportPanel :form="form" :meta="meta" :session="session" :busy="busy"
                       :locked="locked" :sap-busy="sapBusy"
                       @start="startOne" @error="fail" @sap-mode-change="changeSapMode" />
          <div class="card tip-card">
            <div class="card-head"><h2>操作提示</h2></div>
            <div class="card-body tight">
              <ul class="tip-list">
                <li>一次只处理<strong>一张</strong>发票：在清单点「识别这张」，完成前不能开下一张。</li>
                <li>每张需要<strong>两次人工确认</strong>：先核对识别结果，再核对过账载荷并确认过账；系统不会自动连续处理。</li>
                <li>若<strong>场景未命中</strong>，按业务规则中断，不进入本体推理与过账：在「快捷操作」点「确认」即可正常结束本张，也可以点「重新识别这张」重新识别。</li>
                <li>过账完成后在右侧「快捷操作」点「识别下一张」，或在清单里点另一行的「识别这张」；已过账的发票不会重复处理。</li>
                <li>顶部「当前任务」只是状态提示，<strong>所有操作按钮都集中在右侧「快捷操作」</strong>，不再放在抬头。</li>
                <li>清单里的「记录」可回看任意一张的完整处理记录；台账任意一行可查看凭证与审计日志。</li>
              </ul>
              <div class="db-note">
                数据库 <span class="mono">{{ meta?.db_path || '读取中…' }}</span>
                <span v-if="meta"> · 支持 {{ (meta.supported_exts || []).join(' / ') }}</span>
              </div>
            </div>
          </div>
        </div>

        <div id="chat-zone" class="col-right">
          <ChatPanel :session="session" :busy="busy || scanBusy" :locked="locked" :can-next="stats.pending > 0"
                     @action="onAction" @send="onSend" />
        </div>
      </div>

      <!-- 台账：全宽，表格不再被挤压 -->
      <section id="ledger-zone" class="ledger-zone">
        <QueryPanel :refresh-key="refreshKey" :focus-request="focusRequest" @error="fail" />
      </section>
    </main>

    <InvoiceEditForm v-if="editorOpen && session?.draft" :draft="session.draft"
                     @submit="submitEdit" @close="editorOpen = false" />
    <AccountPanel v-if="accountOpen" :user="user" @close="accountOpen = false"
                  @relogin="onPasswordChanged" @error="onAccountError" />
  </div>

  <div v-if="toast" class="toast" :class="toast.type === 'error' ? 'error' : toast.type === 'success' ? 'success' : ''">
    {{ toast.message }}
  </div>
</template>
