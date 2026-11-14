<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, default: '' },
})

const tone = computed(() => {
  const s = (props.status || '').toUpperCase()
  if (['POSTED', 'COMPLETED', 'ACTIVE', 'REASONED', 'OK', 'READY'].includes(s)) return 'success'
  if (['BLOCKED', 'FAILED', 'REJECTED', 'POST_FAILED', 'ERROR', 'CANCELLED'].includes(s)) return 'danger'
  if (['WAITING', 'RUNNING', 'CREATED', 'AWAIT_APPROVAL', 'AWAIT_OCR_CONFIRM', 'ACKNOWLEDGED'].includes(s)) return 'info'
  if (['OCR_DONE', 'SCENARIO', 'REASONING', 'INIT'].includes(s)) return 'purple'
  if (s === 'SKIPPED') return 'warn'
  return 'muted'
})
</script>

<template>
  <span class="badge" :class="`badge-${tone}`">{{ status || '—' }}</span>
</template>
