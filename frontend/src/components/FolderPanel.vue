<script setup>
/**
 * 发票文件夹清单：只负责「选文件夹 → 出清单 → 由人点选一张」。
 * 不做批量：没有启动/停止/进度轮询，识别一张后必须人工确认两次，
 * 完成一张后由人手动点「识别下一张」或点清单里另一行的「识别这张」。
 */
const props = defineProps({
  form: { type: Object, required: true },        // 共享表单：folder / ocrMode / recursive
  scan: { type: Object, default: null },         // /files/scan 只读结果
  rows: { type: Array, default: () => [] },      // App 合并后的逐张清单
  stats: { type: Object, default: () => ({ total: 0, done: 0, pending: 0 }) },
  selectedKey: { type: String, default: '' },   // 当前选中的文件（规范化路径）
  locked: { type: Boolean, default: false },    // 有发票正在流程中，不能再开新的一张
  busy: { type: Boolean, default: false },
  scanBusy: { type: Boolean, default: false },
})
const emit = defineEmits(['scan', 'select', 'start', 'openSession', 'next'])

const BADGE = {
  PENDING: 'badge-muted', ACTIVE: 'badge-info', COMPLETED: 'badge-success',
  ACKNOWLEDGED: 'badge-info', FAILED: 'badge-danger', AWAIT_ACK: 'badge-warn',
  SKIPPED: 'badge-warn', CANCELLED: 'badge-warn',
}

function badge(status) { return BADGE[status] || 'badge-muted' }
function short(v) {
  if (!v) return '—'
  const s = String(v)
  return s.length > 22 ? `${s.slice(0, 9)}…${s.slice(-8)}` : s
}
function canStart(row) { return !props.busy && !props.locked && !row.isActive && !row.done }
function startTitle(row) {
  if (props.locked) return '请先完成当前这张发票的人工确认，再处理下一张'
  if (row.done) return '该发票已处理完成（已过账或已确认结束），不重复处理；需要重做请先在台账核对'
  if (row.isActive) return '这张发票正在流程中'
  return '识别这张发票，随后逐步人工确认'
}
</script>

<template>
  <section id="folder-zone" class="card folder-card">
    <div class="card-head">
      <h2>
        <span class="step-num">1</span> 选择发票文件夹
        <span class="hint">扫描清单 · 一张一张人工确认处理</span>
      </h2>
      <div class="folder-head-right">
        <span v-if="scan" class="badge badge-info">共 {{ stats.total }} 张</span>
        <span v-if="stats.posted" class="badge badge-success">已过账 {{ stats.posted }}</span>
        <span v-if="stats.closed" class="badge badge-info">已确认结束 {{ stats.closed }}</span>
        <span v-if="stats.pending" class="badge badge-muted">待处理 {{ stats.pending }}</span>
      </div>
    </div>

    <div class="card-body">
      <div class="folder-top">
        <label class="field folder-path-field">
          <span>发票文件夹路径</span>
          <div class="folder-input-row">
            <input v-model="form.folder" class="input mono" :disabled="busy"
                   placeholder="D:\workbuddy\qddev\invoices（留空使用默认发票目录）"
                   @keyup.enter="emit('scan')" />
            <button class="btn btn-primary" :disabled="busy || scanBusy" @click="emit('scan')">
              {{ scanBusy ? '扫描中…' : '扫描文件夹' }}
            </button>
          </div>
        </label>
      </div>

      <div class="folder-options">
        <label class="check">
          <input type="checkbox" v-model="form.recursive" :disabled="busy" /> 包含子文件夹
        </label>
        <span class="folder-ocr">OCR 模式：<strong>{{ form.ocrMode }}</strong>（可在下方修改）</span>
        <span style="margin-left:auto"></span>
        <button class="btn btn-sm btn-primary" :disabled="busy || locked || !stats.pending"
                :title="locked ? '请先完成当前这张发票' : '识别下一张待处理发票，之后仍需逐步人工确认'"
                @click="emit('next')">
          识别下一张 →
        </button>
      </div>

      <div v-if="scan" class="notice" style="margin: 10px 0">
        {{ scan.message }}
        点某行的「识别这张」开始，之后<strong>确认识别结果</strong>与<strong>确认过账</strong>都要人工点击；
        系统不会自动连续处理下一张。
      </div>
      <div v-if="scan && stats.total && !stats.pending" class="notice warn" style="margin: 0 0 10px">
        该文件夹的 {{ stats.total }} 张发票均已处理完成（已过账或已确认结束），当前没有待处理的发票。
        把新发票放进该文件夹后点「扫描文件夹」，或换一个文件夹路径再扫描即可继续逐张处理。
      </div>

      <div v-if="rows.length" class="table-scroll folder-table">
        <table class="data">
          <thead>
            <tr>
              <th style="width:44px">#</th>
              <th>发票文件</th>
              <th style="width:118px">状态</th>
              <th style="width:172px">发票号</th>
              <th style="width:160px">凭证号</th>
              <th>说明</th>
              <th style="width:152px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.key"
                :class="{ 'row-active': r.isActive, 'row-selected': r.key === selectedKey }"
                @click="emit('select', r)">
              <td class="muted">{{ r.index }}</td>
              <td class="mono" :title="r.path">{{ r.name }}</td>
              <td><span class="badge" :class="badge(r.status)">{{ r.label }}</span></td>
              <td class="mono">{{ short(r.invoiceNumber) }}</td>
              <td class="mono">{{ r.docNo || '—' }}</td>
              <td class="folder-msg" :title="r.message">{{ r.message || '—' }}</td>
              <td class="folder-actions" @click.stop>
                <button class="btn btn-sm btn-primary" :disabled="!canStart(r)" :title="startTitle(r)"
                        @click="emit('start', r.path)">识别这张</button>
                <button v-if="r.sessionId" class="btn btn-sm" :disabled="busy || locked"
                        @click="emit('openSession', r.sessionId)">记录</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="folder-empty">
        填写<strong>发票文件夹路径</strong>并点「扫描文件夹」，系统会列出可识别的发票。<br />
        之后由你一次点选一张：识别 → 人工核对确认 → 人工确认过账 → 手动处理下一张。<br />
        <span class="muted">没有批量自动处理，任何一张都不会在无人确认的情况下过账。</span>
      </div>
    </div>
  </section>
</template>
