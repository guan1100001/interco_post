/** API 端点集中定义：组件不硬编码 URL，令牌状态只在 client 模块内维护。 */
import request, { setAccessToken } from './client'

const P = '/api/v1'

export const api = {
  // ---- 认证与账号 ----
  login: async (username, password) => {
    const data = await request(`${P}/auth/login`, {
      method: 'POST', body: { username, password },
    })
    setAccessToken(data.access_token)   // 仅内存，不落 localStorage
    return data.user
  },
  me: () => request(`${P}/auth/me`),
  logout: () => request(`${P}/auth/logout`, { method: 'POST' }),
  changePassword: (oldPassword, newPassword) =>
    request(`${P}/auth/password`, {
      method: 'POST', body: { old_password: oldPassword, new_password: newPassword },
    }),
  listUsers: () => request(`${P}/auth/users`),
  createUser: (payload) => request(`${P}/auth/users`, { method: 'POST', body: payload }),
  updateUser: (id, patch) => request(`${P}/auth/users/${id}`, { method: 'PATCH', body: patch }),

  // ---- 系统 ----
  meta: () => request(`${P}/meta`),
  health: () => request('/health'),
  // 运行时过账模式：进程内立即生效，重启回落 .env 的 SAP_MODE
  getRuntimeSettings: () => request(`${P}/settings`),
  setSapMode: (sapMode) =>
    request(`${P}/settings/sap-mode`, { method: 'PUT', body: { sap_mode: sapMode } }),

  // ---- 文件与流程（扫描只读；识别与过账一律逐张人工触发） ----
  checkPath: (filePath) =>
    request(`${P}/files/check`, { method: 'POST', body: { file_path: filePath } }),
  browse: (directory = '') => request(`${P}/files/browse`, { params: { directory } }),
  scanFolder: (directory = '', recursive = false) =>
    request(`${P}/files/scan`, { params: { directory, recursive } }),


  createSession: (filePath, ocrMode) =>
    request(`${P}/pipeline/sessions`, {
      method: 'POST', body: { file_path: filePath, ocr_mode: ocrMode || undefined },
    }),
  getSession: (id) => request(`${P}/pipeline/sessions/${encodeURIComponent(id)}`),
  listSessions: (limit = 20) =>
    request(`${P}/pipeline/sessions`, { params: { limit } }),
  sendMessage: (id, payload) =>
    request(`${P}/pipeline/sessions/${encodeURIComponent(id)}/messages`, {
      method: 'POST', body: payload,
    }),

  // ---- 台账 ----
  listInvoices: (params) => request(`${P}/invoices`, { params }),
  getInvoice: (number) => request(`${P}/invoices/${encodeURIComponent(number)}`),
  stats: () => request(`${P}/invoices/stats/overview`),
  statusOptions: () => request(`${P}/invoices/status/options`),
}
