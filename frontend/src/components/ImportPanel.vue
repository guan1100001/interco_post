<script setup>
/**
 * 当前发票与识别设置：只处理「一张」发票。
 * 路径通常来自上方文件夹清单的点选；也保留手动填写 / 目录浏览作为补充入口。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/endpoints'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  // 共享表单状态（由 App 持有），此处只改对象属性，不替换对象本身。
  form: { type: Object, required: true },
  meta: { type: Object, default: null },
  session: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  locked: { type: Boolean, default: false },   // 已有发票在流程中，不允许再开一张
  sapBusy: { type: Boolean, default: false },  // 正在向后端切换过账模式
})
const emit = defineEmits(['start', 'error', 'sapModeChange'])

const directory = ref('')
const checkResult = ref(null)
const browseResult = ref(null)
const showBrowser = ref(false)
const localBusy = ref(false)

watch(() => props.meta?.ocr_mode, (v) => { if (v) props.form.ocrMode = v }, { immediate: true })

const SAP_OPTIONS = [
  { value: 'mock', label: 'mock · 模拟过账（不连真实 SAP）' },
  { value: 'dry_run', label: 'dry_run · 仅预览载荷（不写凭证）' },
  { value: 'live', label: 'live · 真实 SAP 过账' },
]
const SAP_HINT = {
  mock: '模拟过账：生成模拟凭证并写入本地数据库，不会连接真实 SAP。',
  dry_run: '预览模式：只校验与预览过账载荷，不写入凭证、不调用 SAP。',
  live: '真实 SAP 过账：点「确认过账」将调用 ZfmAico002 执行双主体同步过账，不可撤销。',
}
const isLive = computed(() => props.form.sapMode === 'live')
const sapMissing = computed(() => props.meta?.sap_missing_config || [])

// 选中即切换：live 不再弹二次确认，成功/失败一律以后端返回为准。
function onSapModeSelect(e) {
  const next = e.target.value
  // 后端确认前立刻把 DOM 值回滚，避免界面显示成「已切换」而实际没生效。
  e.target.value = props.form.sapMode
  if (next === props.form.sapMode) return
  emit('sapModeChange', next)
}

onMounted(async () => {
  try {
    const res = await api.browse('')
    browseResult.value = res
    directory.value = res.directory
  } catch {
    // 目录不存在或后端未就绪时静默降级，用户仍可手动填写 / 浏览目录
  }
})

// 「识别这一张发票」是显式的人工重新识别入口：即便当前还有一张在流程中，
// 也必须可点，点击后由 App 重新调用 OCR（同一张重启会话、不同张开新会话）。
// 只在真正请求后端期间（busy）置灰，避免连点重复发起。
const canStart = computed(() => !!props.form.path.trim() && !props.busy)
const checkTone = computed(() => {
  const r = checkResult.value
  if (!r) return ''
  if (r.kind === 'directory') return ''
  return r.exists && r.supported ? 'success' : 'error'
})

async function checkPath() {
  if (!props.form.path.trim()) return
  localBusy.value = true
  try {
    checkResult.value = await api.checkPath(props.form.path.trim())
  } catch (e) {
    emit('error', e.friendly)
  } finally {
    localBusy.value = false
  }
}

async function loadBrowser() {
  localBusy.value = true
  try {
    browseResult.value = await api.browse(directory.value)
    directory.value = browseResult.value.directory
  } catch (e) {
    emit('error', e.friendly)
  } finally {
    localBusy.value = false
  }
}

async function openBrowser() {
  showBrowser.value = !showBrowser.value
  if (showBrowser.value && !browseResult.value) await loadBrowser()
}

function pick(f) {
  props.form.path = f.path
  checkResult.value = null
  showBrowser.value = false
}

function start() {
  const path = String(props.form.path || '').trim()
  if (!path) { emit('error', '请先选择或填写发票文件路径'); return }
  // 只传路径字符串：App 的 startOne(filePath, ocrMode) 会据此调用
  // POST /pipeline/sessions，由后端立即执行 OCR 识别（fetch_ocr → transform → 质量校验）。
  // 之前这里传的是对象 {file_path, ocr_mode}，被当成路径发到后端，识别根本没有执行。
  emit('start', path, { ocrMode: props.form.ocrMode, force: true })
}
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h2><span class="step-num">2</span> 当前发票与识别设置</h2>
      <StatusBadge v-if="session" :status="session.status" />
    </div>
    <div class="card-body">
      <label class="field">
        <span>当前选中的发票文件</span>
        <input v-model="form.path" class="input mono"
               placeholder="在上方清单点一行即可选中，或粘贴某个发票文件的完整路径"
               :disabled="busy" @keyup.enter="start" />
      </label>
      <div class="field-hint">
        在上方<strong>发票清单</strong>点选一行即可选中；一次只识别这一张，不会连带处理文件夹里其它发票。
      </div>

      <div class="form-grid">
        <label class="field">
          <span>OCR 模式</span>
          <select v-model="form.ocrMode" class="input" :disabled="busy || locked">
            <option value="auto">auto · 服务不可用时自动降级</option>
            <option value="live">live · 仅调用真实 OCR</option>
            <option value="mock">mock · 使用模拟识别数据</option>
          </select>
        </label>
        <label class="field">
          <span>过账模式 <em class="field-tag">可修改</em></span>
          <select class="input" :class="{ 'sap-select-live': isLive }"
                  :value="form.sapMode" :disabled="sapBusy || !meta"
                  title="切换后本服务立即生效；重启后端会回落到 .env 里的 SAP_MODE"
                  @change="onSapModeSelect">
            <option v-for="o in SAP_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </label>
      </div>

      <div class="field-hint" style="margin: 2px 0 0">
        上方 <strong>OCR 模式</strong>决定识别数据来源（live 直连内网 OCR，程序已自动绕过本机系统代理）；
        下方 <strong>过账模式</strong>决定「确认过账」走模拟 / 预览 / 真实 SAP，<strong>选中即生效</strong>。
      </div>

      <div class="sap-mode-hint" :class="{ live: isLive }">
        <strong>{{ form.sapMode }}</strong> · {{ SAP_HINT[form.sapMode] || '模式读取中…' }}
        <span v-if="meta?.sap_mode_runtime_override" class="muted">
          （运行时设置，仅本次服务有效；重启回落 .env 的 SAP_MODE）
        </span>
        <div v-if="isLive && sapMissing.length" class="sap-missing">
          缺少 SAP 连接配置：<span class="mono">{{ sapMissing.join('、') }}</span>；
          补齐 .env 并重启后端后才能真实过账。
        </div>
      </div>

      <div class="btn-row">
        <button class="btn btn-sm" :disabled="!form.path.trim() || localBusy" @click="checkPath">校验路径</button>
        <button class="btn btn-sm" :disabled="localBusy" @click="openBrowser">
          {{ showBrowser ? '收起目录' : '浏览目录' }}
        </button>
      </div>

      <button class="btn btn-primary btn-block folder-start-btn" :disabled="!canStart"
              title="调用 OCR 识别当前选中的这一张发票，识别结果会显示在右侧处理记录里"
              @click="start">
        {{ busy ? '正在调用 OCR 识别…' : '识别这一张发票' }} <span aria-hidden="true">→</span>
      </button>
      <div class="field-hint" style="margin: 6px 0 0">
        点这里会<strong>真正调用 OCR 识别这一张发票</strong>（按上方 OCR 模式）；
        识别完成后<strong>必须先人工核对并确认</strong>，推理校验通过后还要<strong>再人工确认过账</strong>，两步都不能跳过。
      </div>

      <div v-if="checkResult" class="notice" :class="checkTone" style="margin-top:10px">
        {{ checkResult.message }} · {{ checkResult.file_path }}
        <span v-if="checkResult.exists && checkResult.kind !== 'directory'">（{{ checkResult.size }} bytes）</span>
      </div>

      <div v-if="showBrowser" class="browser-box">
        <div class="btn-row" style="margin-bottom:8px">
          <input v-model="directory" class="input mono" style="flex:1;min-width:160px" placeholder="目录路径，留空用默认目录" />
          <button class="btn btn-sm" :disabled="localBusy" @click="loadBrowser">刷新</button>
        </div>
        <div v-if="!browseResult" class="muted">正在读取目录…</div>
        <div v-else-if="!browseResult.files.length" class="muted">该目录下没有文件。</div>
        <div v-else class="table-scroll browser-list">
          <table class="data">
            <thead><tr><th>文件名</th><th>大小</th><th>类型</th></tr></thead>
            <tbody>
              <tr v-for="f in browseResult.files" :key="f.path" @click="pick(f)">
                <td>{{ f.name }}</td>
                <td>{{ f.size }}</td>
                <td><span class="badge" :class="f.supported ? 'badge-success' : 'badge-muted'">
                  {{ f.supported ? '可识别' : '不支持' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="field-hint" style="margin:6px 0 0">点击文件名即可选中，选中后点「识别这一张发票」。</p>
      </div>

      <div v-if="session" class="notice" style="margin-top:12px">
        当前会话 <span class="mono">{{ session.id.slice(0, 8) }}</span>
        · 阶段 <strong>{{ session.stage }}</strong>
        · 发票号 <span class="mono">{{ session.invoice_number || '待确认' }}</span>
        <span v-if="session.context?.ocr_source"> · 数据来源 {{ session.context.ocr_source }}</span>
      </div>
    </div>
  </div>
</template>
