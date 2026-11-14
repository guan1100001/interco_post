<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api/endpoints'

defineProps({ notice: { type: String, default: '' } })
const emit = defineEmits(['success'])

const username = ref('')
const password = ref('')
const showPwd = ref(false)
const error = ref('')
const busy = ref(false)

onMounted(() => {
  // 仅记住用户名方便输入；任何令牌/密码都不写入本地存储。
  username.value = localStorage.getItem('login_username') || ''
})

async function submit() {
  if (busy.value) return
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  busy.value = true
  try {
    const user = await api.login(username.value.trim(), password.value)
    localStorage.setItem('login_username', username.value.trim())
    password.value = ''
    emit('success', user)
  } catch (e) {
    error.value = e.friendly || '登录失败，请稍后再试'
    password.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-mark" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <path d="M5 4h11l3 3v13H5z" stroke="#fff" stroke-width="1.8" stroke-linejoin="round" />
            <path d="M8 10h8M8 14h8M8 18h5" stroke="#fff" stroke-width="1.6" stroke-linecap="round" />
          </svg>
        </div>
        <div>
          <div class="login-brand-name">关联交易发票处理台</div>
          <div class="login-brand-sub">INVOICE PROCESSING CONSOLE · 授权访问</div>
        </div>
      </div>

      <div class="login-body">
        <h1>登录系统</h1>
        <p class="login-sub">请使用管理员分配的账号登录。未授权用户无法查看发票数据或执行过账。</p>

        <div v-if="notice" class="login-notice success">{{ notice }}</div>
        <div v-if="error" class="login-notice error">{{ error }}</div>

        <form @submit.prevent="submit">
          <label class="field">
            <span>用户名</span>
            <input v-model="username" class="input" autocomplete="username" autofocus
                   placeholder="请输入用户名" :disabled="busy" />
          </label>
          <label class="field">
            <span>密码</span>
            <div class="pwd-row">
              <input v-model="password" class="input" :type="showPwd ? 'text' : 'password'"
                     autocomplete="current-password" placeholder="请输入密码" :disabled="busy" />
              <button type="button" class="pwd-toggle" @click="showPwd = !showPwd">
                {{ showPwd ? '隐藏' : '显示' }}
              </button>
            </div>
          </label>
          <button class="btn btn-primary login-submit" type="submit" :disabled="busy">
            <span v-if="busy" class="spin light"></span>{{ busy ? '正在登录…' : '登录 →' }}
          </button>
        </form>

        <div class="login-foot">
          <span class="lock-dot"></span>
          密码以 PBKDF2 加盐摘要存储；登录凭证仅保存在当前浏览器会话内存中。
        </div>
      </div>
    </div>
  </div>
</template>
