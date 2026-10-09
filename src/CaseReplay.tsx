import { useEffect, useState } from 'react';
import { api } from './api';
import { Button } from './components';
import type { Runtime } from './useRuntime';
import type { Execution } from './runtime';

type Replay = {
  run_id: string;
  through_seq: number;
  current_seq: number;
  next_after: number | null;
  commit: string | null;
  status: string;
  task: { goal: string; allowed_paths: string[]; deliverables: string[] };
  bindings: { model: string; implementation_digest: string; harness: object };
  verification: { verdict?: string } | null;
  events: { seq: number; type: string; time: string; message: string }[];
};
export function CaseReplay({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const [data, setData] = useState<Replay | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    api<Replay>(`/runs/${r.id}/case-replay`, 'GET', undefined, undefined, {
      signal: controller.signal,
    })
      .then(setData)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [r.id]);
  async function more() {
    if (!data || data.next_after === null) return;
    setBusy(true);
    try {
      const next = await api<Replay>(
        `/runs/${r.id}/case-replay?after=${data.next_after}&through_seq=${data.through_seq}`,
      );
      setData({ ...data, events: [...data.events, ...next.events], next_after: next.next_after });
      setError('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="案例回放">
      <h2>案例回放</h2>
      <p className="muted">
        只读展示已提交的约束、动作、故障与验收摘要；浏览不会重新调用模型或工具。
      </p>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <dl>
            <dt>任务约束</dt>
            <dd>{data.task.goal}</dd>
            <dt>允许修改</dt>
            <dd>{data.task.allowed_paths.join('、')}</dd>
            <dt>必需交付</dt>
            <dd>{data.task.deliverables.join('、')}</dd>
            <dt>仓库 commit</dt>
            <dd className="mono wrap">{data.commit || '内联快照，无 Git commit'}</dd>
            <dt>模型</dt>
            <dd>{data.bindings.model || '未配置'}</dd>
            <dt>验收</dt>
            <dd>{data.verification?.verdict || '尚未验收'}</dd>
          </dl>
          <p className="muted">
            事件范围 1–{data.through_seq} · 已加载 {data.events.length} 条 · 状态 {data.status}
          </p>
          <ol className="replay-events">
            {data.events.map((event) => (
              <li key={event.seq}>
                <strong>
                  #{event.seq} {event.type}
                </strong>
                <p>{event.message}</p>
                <time>{event.time}</time>
              </li>
            ))}
          </ol>
          {data.next_after !== null && (
            <Button disabled={busy} onClick={() => void more()}>
              加载后续回放事件
            </Button>
          )}
          <div className="button-row">
            <Button onClick={() => rt.setTab('changes')}>查看补丁与交付物</Button>
            <Button onClick={() => rt.setTab('verification')}>查看独立验收证据</Button>
            <Button
              onClick={() =>
                rt.app.download(`${r.id}-replay-loaded.json`, JSON.stringify(data, null, 2))
              }
            >
              导出已加载回放
            </Button>
          </div>
          <details>
            <summary>固定执行配置</summary>
            <pre className="code-block">{JSON.stringify(data.bindings, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
