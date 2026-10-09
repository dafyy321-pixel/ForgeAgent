import { ApiError } from './api';

export async function followEvents(
  runId: string,
  signal: AbortSignal,
  onEvent: (seq: number) => void,
) {
  let cursor = 0;
  let failures = 0;
  while (!signal.aborted && failures < 8) {
    const token = sessionStorage.getItem('forge-access-token');
    try {
      const response = await fetch(`/v1/runs/${encodeURIComponent(runId)}/events`, {
        signal,
        headers: {
          'Last-Event-ID': String(cursor),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
      if (!response.ok) {
        const payload = await response
          .json()
          .catch(() => ({ code: 'STREAM_HTTP', message: `HTTP ${response.status}` }));
        throw new ApiError(payload, response.status);
      }
      if (!response.body) throw new Error('Missing event stream');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        while (!signal.aborted) {
          let idle: ReturnType<typeof setTimeout> | undefined;
          const chunk = await Promise.race([
            reader.read(),
            new Promise<never>((_, reject) => {
              idle = setTimeout(() => reject(new Error('Event stream idle timeout')), 45000);
            }),
          ]).finally(() => clearTimeout(idle));
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, '\n');
          if (buffer.length > 1024 * 1024) throw new Error('Event frame exceeds limit');
          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            let event = 'domain';
            let id = 0;
            for (const line of frame.split('\n')) {
              if (line.startsWith('event:')) event = line.slice(6).trim();
              if (line.startsWith('id:')) id = Number(line.slice(3).trim());
            }
            if (event === 'end') return;
            if (event === 'auth_expired' || event === 'authorization_error')
              throw new ApiError(
                { code: 'UNAUTHORIZED', message: '事件身份已到期或权限已撤回' },
                event === 'auth_expired' ? 401 : 403,
              );
            if (Number.isSafeInteger(id) && id > cursor) {
              cursor = id;
              failures = 0;
              onEvent(id);
            }
          }
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } catch (error) {
      if (signal.aborted) return;
      if (
        error instanceof ApiError &&
        error.status === 401 &&
        sessionStorage.getItem('forge-access-token') !== token
      )
        continue;
      if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) throw error;
      if (error instanceof ApiError && error.status === 409) cursor = 0;
    }
    failures++;
    await new Promise<void>((resolve) => {
      const finish = () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', finish);
        resolve();
      };
      const timer = setTimeout(finish, Math.min(10000, 250 * 2 ** (failures - 1)));
      signal.addEventListener('abort', finish, { once: true });
    });
  }
  if (!signal.aborted)
    throw new ApiError(
      { code: 'STREAM_DISCONNECTED', message: '事件连接暂停，正在通过版本检查同步' },
      503,
    );
}
