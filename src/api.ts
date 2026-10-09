export interface ErrorPayload {
  code?: string;
  message?: string;
  retryable?: boolean;
  correlation_id?: string;
  errors?: { message: string }[];
  [key: string]: unknown;
}
export class ApiError extends Error {
  constructor(
    public payload: ErrorPayload,
    public status: number,
    public uncertain = false,
  ) {
    super(
      `${payload.code || status}: ${payload.message || '请求失败'}${payload.errors ? ' · ' + payload.errors.map((e) => e.message).join('; ') : ''}`,
    );
    this.name = 'ApiError';
  }
}
type Options = { signal?: AbortSignal; timeoutMs?: number; format?: 'json' | 'text'; raw?: Blob };
const mutations = new Map<string, string>();
async function signatureFor(value: string) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return (
    'forge-mutation-' +
    Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('')
  );
}
function keyFor(signature: string) {
  let key = mutations.get(signature) || sessionStorage.getItem(signature);
  if (!key) {
    key = crypto.randomUUID();
    mutations.set(signature, key);
    sessionStorage.setItem(signature, key);
  }
  return key;
}
function forget(signature: string) {
  mutations.delete(signature);
  sessionStorage.removeItem(signature);
}
export async function api<T = any>(
  path: string,
  method = 'GET',
  body?: unknown,
  key?: string,
  options: Options = {},
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  options.signal?.addEventListener('abort', cancel, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? 30000);
  const token = sessionStorage.getItem('forge-access-token');
  let principal = 'local';
  try {
    if (token) {
      const claims = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
      principal = JSON.stringify([claims.iss, claims.sub, claims.tenant_id]);
    }
  } catch {
    principal = token || 'local';
  }
  let signature = '';
  const mutation = method !== 'GET' && method !== 'HEAD';
  try {
    signature = await signatureFor(
      principal + ' ' + method + ' ' + path + ' ' + JSON.stringify(body ?? null),
    );
    const mutationKey = key || (mutation ? keyFor(signature) : undefined);
    const response = await fetch(`/v1${path}`, {
      method,
      signal: controller.signal,
      headers: {
        ...(options.raw
          ? { 'Content-Type': 'application/octet-stream' }
          : body !== undefined
            ? { 'Content-Type': 'application/json' }
            : {}),
        ...(mutationKey ? { 'Idempotency-Key': mutationKey } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: options.raw ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
    if (!response.ok) {
      const payload: ErrorPayload = await response
        .json()
        .catch(() => ({ code: 'HTTP_ERROR', message: `HTTP ${response.status}` }));
      if (response.status < 500 && ![408, 429].includes(response.status)) forget(signature);
      throw new ApiError(payload, response.status, response.status >= 500);
    }
    const value = (
      options.format === 'text'
        ? await response.text()
        : response.status === 204
          ? null
          : await response.json()
    ) as T;
    forget(signature);
    return value;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted)
      throw new ApiError(
        {
          code: timedOut ? 'REQUEST_TIMEOUT' : 'REQUEST_CANCELLED',
          message: timedOut ? '请求超时；写入结果可能需要查询确认' : '请求已取消',
        },
        0,
        mutation,
      );
    throw new ApiError(
      { code: 'NETWORK_ERROR', message: '连接中断；保留操作键，可安全重试支持幂等的请求' },
      0,
      mutation,
    );
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', cancel);
  }
}
