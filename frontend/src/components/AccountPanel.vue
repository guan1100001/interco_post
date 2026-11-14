<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/endpoints'

const props = defineProps({ user: { type: Object, required: true } })
const emit = defineEmits(['close', 'relogin', 'error'])

const ROLE_LABEL = { admin: '管理员', operator: '操作员', viewer: '只读' }
const isAdmin = computed(() => props.user?.role === 'admin')
const tab = ref('pwd')

// 修改密码
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const pwdBusy = ref(false)
const pwdError = ref('')

async function changePwd() {
  pwdError.value = ''
  if (!oldPassword.value || !newPassword.value) { pwdError.value = '请填写原密码和新密码'; return }
  if (newPassword.value.length < 8) { pwdError.value = '新密码至少 8 位'; return }
  if (newPassword.value !== confirmPassword.value) { pwdError.value = '两次输入的新密码不一致'; return }
  pwdBusy.value = true
  try {
    await api.changePassword(oldPassword.value, newPassword.value)
    emit('relogin')
  } catch (e) {
    pwdError.value = e.friendly || '修改失败'
  } finally {
    pwdBusy.value = false
  }
}

// 账号管理（仅管理员）
const users = ref([])
const usersLoading = ref(false)
const createForm = ref({ username: '', display_name: '', role: 'operator', password: '' })
const createBusy = ref(false)
const createError = ref('')
const resetFor = ref(null)
const resetPwd = ref('')

async function loadUsers() {
  usersLoading.value = true
  try { users.value = await api.listUsers() } catch (e) { emit('error', e.friendly) }
  finally { usersLoading.value = false }
}

async function createUser() {
  createError.value = ''
  const f = createForm.value
  if (!f.username.trim() || f.password.length < 8) {
    createError.value = '请填写用户名，密码至少 8 位'
    return
  }
  createBusy.value = true
  try {
    await api.createUser(f)
    createForm.value = { username: '', display_name: '', role: 'operator', password: '' }
    createError.value = ''
    await loadUsers()
  } catch (e) {
    createError.value = e.friendly || '创建失败'
  } finally {
    createBusy.value = false
  }
}

async function patchUser(u, patch) {
  try {
    await api.updateUser(u.id, patch)
    await loadUsers()
  } catch (e) {
    emit('error', e.friendly || '操作失败')
  }
}

async function submitReset() {
  if (!resetFor.value || resetPwd.value.length < 8) {
    createError.value = '重置密码至少 8 位'
    return
  }
  await patchUser(resetFor.value, { password: resetPwd.value })
  resetFor.value = null
  resetPwd.value = ''
}

onMounted(() => { if (isAdmin.value) loadUsers() })
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal account-modal">
      <div class="modal-head">
        <h3>账号与安全 <span class="mono" style="font-weight:400;color:var(--muted)">{{ user.username }}</span></h3>
        <button class="icon-btn" @click="emit('close')">×</button>
      </div>

      <div class="account-tabs">
        <button class="tab-btn" :class="{ active: tab === 'pwd' }" @click="tab = 'pwd'">修改密码</button>
        <button v-if="isAdmin" class="tab-btn" :class="{ active: tab === 'users' }" @click="tab = 'users'">
          账号管理 <span class="tab-count">{{ users.length }}</span>
        </button>
        <span class="tab-role">当前角色：<strong>{{ ROLE_LABEL[user.role] || user.role }}</strong></span>
      </div>

      <div class="modal-body">
        <!-- 修改密码 -->
        <div v-if="tab === 'pwd'">
          <div class="notice" style="margin-bottom:14px">
            修改成功后会撤销全部登录会话，需要使用新密码重新登录。
          </div>
          <div v-if="pwdError" class="notice error" style="margin-bottom:12px">{{ pwdError }}</div>
          <div class="form-grid">
            <label class="field"><span>原密码</span>
              <input v-model="oldPassword" class="input" type="password" autocomplete="current-password" /></label>
            <label class="field"><span>新密码（至少 8 位）</span>
              <input v-model="newPassword" class="input" type="password" autocomplete="new-password" /></label>
            <label class="field"><span>确认新密码</span>
              <input v-model="confirmPassword" class="input" type="password" autocomplete="new-password"
                     @keyup.enter="changePwd" /></label>
          </div>
          <div class="btn-row">
            <button class="btn btn-primary" :disabled="pwdBusy" @click="changePwd">
              <span v-if="pwdBusy" class="spin light"></span>确认修改
            </button>
          </div>
        </div>

        <!-- 账号管理 -->
        <div v-else-if="tab === 'users' && isAdmin">
          <div class="section-title">新增账号</div>
          <div class="user-create-grid">
            <input v-model="createForm.username" class="input" placeholder="用户名" />
            <input v-model="createForm.display_name" class="input" placeholder="显示名（可选）" />
            <select v-model="createForm.role" class="input">
              <option value="operator">操作员</option>
              <option value="viewer">只读</option>
              <option value="admin">管理员</option>
            </select>
            <input v-model="createForm.password" class="input" type="password" placeholder="初始密码（≥8位）" />
            <button class="btn btn-primary" :disabled="createBusy" @click="createUser">
              <span v-if="createBusy" class="spin light"></span>创建
            </button>
          </div>
          <div v-if="createError" class="notice error" style="margin:10px 0">{{ createError }}</div>

          <div class="section-title">现有账号 <span class="muted" style="font-weight:400">· 停用后该账号立即无法登录</span></div>
          <div class="table-scroll">
            <table class="data">
              <thead><tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="u in users" :key="u.id">
                  <td class="mono">{{ u.username }}<span v-if="u.id === user.id" class="self-tag">本人</span></td>
                  <td>{{ u.display_name || '—' }}</td>
                  <td>
                    <select class="input role-select" :value="u.role" :disabled="u.id === user.id"
                            @change="patchUser(u, { role: $event.target.value })">
                      <option value="admin">管理员</option><option value="operator">操作员</option><option value="viewer">只读</option>
                    </select>
                  </td>
                  <td><span class="badge" :class="u.is_active ? 'badge-success' : 'badge-muted'">
                    {{ u.is_active ? '启用' : '停用' }}</span></td>
                  <td>{{ u.last_login_at || '—' }}</td>
                  <td>
                    <div class="btn-row">
                      <button class="btn btn-sm" :disabled="u.id === user.id"
                              @click="patchUser(u, { is_active: !u.is_active })">
                        {{ u.is_active ? '停用' : '启用' }}
                      </button>
                      <button class="btn btn-sm" @click="resetFor = resetFor?.id === u.id ? null : u; resetPwd = ''">
                        重置密码
                      </button>
                    </div>
                    <div v-if="resetFor?.id === u.id" class="reset-row">
                      <input v-model="resetPwd" class="input" type="password" placeholder="新密码（≥8位）" />
                      <button class="btn btn-sm btn-primary" @click="submitReset">确认</button>
                    </div>
                  </td>
                </tr>
                <tr v-if="!users.length && !usersLoading"><td class="empty" colspan="6">暂无账号</td></tr>
              </tbody>
            </table>
          </div>
          <div v-if="usersLoading" class="muted" style="margin-top:8px">正在加载账号…</div>
        </div>
      </div>

      <div class="modal-foot">
        <button class="btn" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
