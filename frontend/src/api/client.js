/**
 * 类型化 fetch 封装：统一错误结构、request-id、加载/错误边界与认证。
 *
 * 认证约定：
 * - access token 只保存在模块内存变量中，绝不写入 localStorage / sessionStorage / URL；
 * - refresh token 由后端以 HttpOnly Cookie 下发，JS 无法读取；
 * - 401 自动刷新一次并重试原请求；刷新失败则触发 onUnauthorized，由界面回到登录页；
 * - 并发 401 共享同一个刷新 Promise，避免重复轮换令牌。
 *
 * 重试约定：4xx 不重试；5xx 仅对幂等 GET 最多重试 3 次（避免重复过账/重复落库）。
 * Base URL 通过 VITE_API_BASE_URL 注入，默认同源（Vite 代理 / FastAPI 托管均适用）。
 */
const BASE = import.meta.env.VITE_API_BASE_URL || ''
const MAX_RETRY = 3
const AUTH_PUBLIC = [
  '/api/v1/auth/login',
  '/api/v1/auth/refresh',
  '/api/v1/auth/logout',
]
const REFRESH_PATH = '/api/v1/auth/refresh'

const FRIENDLY = {
  BAD_REQUEST: '请求参数有误，请检查输入',
  UNAUTHORIZED: '登录已失效，请重新登录',
  FORBIDDEN: '当前账号没有执行此操作的权限',
  ACCOUNT_LOCKED: '连续登录失败次数过多，账号已临时锁定',
  NOT_FOUND: '未找到对应数据',
  STATE_CONFLICT: '当前流程状态不允许该操作',
  UPSTREAM_ERROR: '上游服务暂时不可用，请稍后再试',
  INTERNAL_ERROR: '服务内部错误，请联系管理员',
  NETWORK_ERROR: '无法连接后端服务，请确认服务已启动',
  PARSE_ERROR: '服务返回数据格式异常',
}

let accessToken = null
let refreshing = null
let unauthorizedHandler = null

export function setAccessToken(token) { accessToken = token || null }
export function getAccessToken() { return accessToken }
export function clearAccessToken() { accessToken = null }
export function onUnauthorized(handler) { unauthorizedHandler = handler }

export class ApiError extends Error {
  constructor(code, message, status = 0, details = null, requestId = '-') {
    super(message)
    this.code = code
    this.status = status
    this.details = details
    this.requestId = requestId
    this.friendly = message || FRIENDLY[code] || '操作失败，请稍后重试'
  }
}

function buildUrl(path, params) {
  const search = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.append(key, String(value))
  })
  const qs = search.toString()
  return `${BASE}${path}${qs ? `?${qs}` : ''}`
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function requestRefresh() {
  const response = await fetch(`${BASE}${REFRESH_PATH}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
  })
  if (!response.ok) throw new Error('refresh_failed')
  const payload = await response.json()
  if (!payload?.access_token) throw new Error('refresh_invalid')
  setAccessToken(payload.access_token)
  return payload
}

function refreshOnce() {
  if (!refreshing) {
    refreshing = requestRefresh().finally(() => { refreshing = null })
  }
  return refreshing
}

async function request(path, options = {}, attempt = 0) {
  const { method = 'GET', body, params, skipRefresh = false, _authRetried = false } = options
  const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  let response
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if (attempt < MAX_RETRY) {
      await sleep(250 * (attempt + 1))
      return request(path, options, attempt + 1)
    }
    throw new ApiError('NETWORK_ERROR', FRIENDLY.NETWORK_ERROR, 0, String(error))
  }

  // 登录态失效：自动刷新一次并重试；刷新失败则通知界面回到登录页
  if (response.status === 401 && !AUTH_PUBLIC.includes(path) && !skipRefresh && !_authRetried) {
    try {
      await refreshOnce()
      return request(path, { ...options, _authRetried: true }, attempt)
    } catch {
      clearAccessToken()
      if (unauthorizedHandler) unauthorizedHandler()
    }
  }

  // 5xx 只对 GET 重试，POST 可能已产生副作用（确认/过账）绝不自动重放
  if (response.status >= 500 && method === 'GET' && attempt < MAX_RETRY) {
    await sleep(300 * (attempt + 1))
    return request(path, options, attempt + 1)
  }

  if (response.status === 204) return null
  let payload
  try {
    payload = await response.json()
  } catch {
    throw new ApiError('PARSE_ERROR', FRIENDLY.PARSE_ERROR, response.status)
  }
  if (!response.ok) {
    const err = payload?.error || {}
    throw new ApiError(
      err.code || `HTTP_${response.status}`,
      err.message || FRIENDLY[err.code] || '请求失败',
      response.status,
      err.details ?? null,
      err.request_id || response.headers.get('X-Request-ID') || '-',
    )
  }
  return payload
}

export const http = { request, buildUrl }
export default request
