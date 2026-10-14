import { useEffect, useState } from 'react';
import { api } from './api';
import { Button, Empty, Panel } from './components';
import type { Execution } from './runtime';
import type { Runtime } from './useRuntime';
import { JsonForm } from './BackendViews';

type RecoveryRecord = { id: string; status: string; created_at: string; data: Record<string, any> };
type RecoveryPage = { items: RecoveryRecord[]; next_cursor?: string | null };

export function RecoveryHistory({ run: r }: { run: Execution }) {
  const [kind, setKind] = useState('diagnoses');
  const [cursor, setCursor] = useState<string | null>(null);
  const [result, setResult] = useState<RecoveryPage>({ items: [] });
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api<RecoveryPage>(
      `/runs/${r.id}/recovery?kind=${kind}&limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      'GET',
      undefined,
      undefined,
      { signal: controller.signal },
    )
      .then(setResult)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [r.id, r.stateVersion, kind, cursor]);
  return (
    <Panel title="证据与恢复记录">
      <p className="muted">记录保留候选原因与未知项；最终完成仍以独立验收为准。</p>
      <div className="button-row">
        {Object.entries({ diagnoses: '诊断', evidence: '证据', attempts: '恢复动作' }).map(
          ([value, label]) => (
            <Button
              key={value}
              onClick={() => {
                setKind(value);
                setCursor(null);
              }}
              disabled={kind === value}
            >
              {label}
            </Button>
          ),
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {!result.items.length && (
        <p className="muted">暂无记录。创建任务时可在高级配置中启用证据驱动恢复。</p>
      )}
      {result.items.map((row) => (
        <details key={row.id}>
          <summary>
            {row.data.reason || row.data.strategy || row.data.source || row.id} ·{' '}
            {row.data.conclusion || row.status}
          </summary>
          <p className="small-note">
            {row.created_at} · {row.id}
          </p>
          <pre>{JSON.stringify(row.data, null, 2)}</pre>
        </details>
      ))}
      <div className="button-row">
        <Button disabled={!cursor} onClick={() => setCursor(null)}>
          首页
        </Button>
        <Button
          disabled={!result.next_cursor}
          onClick={() => setCursor(result.next_cursor || null)}
        >
          下一页
        </Button>
      </div>
    </Panel>
  );
}

export function ActionLedger({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const [actions, setActions] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [evidence, setEvidence] = useState('');
  const [outcome, setOutcome] = useState('occurred');
  const [input, setInput] = useState('');
  const [form, setForm] = useState<{ title: string; path: string; initial: object } | null>(null);
  const [budget, setBudget] = useState<any>(null);
  useEffect(() => {
    api(`/runs/${r.id}/budget`)
      .then(setBudget)
      .catch((e) => setError(e.message));
  }, [r.id, r.stateVersion]);
  useEffect(() => {
    api<typeof actions>(`/runs/${r.id}/actions`)
      .then(setActions)
      .catch((e) => setError(e.message));
  }, [r.id, r.stateVersion]);
  async function reconcile(id: string) {
    try {
      await api(`/actions/${id}/reconcile`, 'POST', {
        expected_version: r.stateVersion,
        reason: '用户人工对账',
        outcome,
        evidence,
      });
      setEvidence('');
      await rt.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function send() {
    try {
      await api(`/runs/${r.id}/input`, 'POST', {
        expected_version: r.stateVersion,
        reason: '用户补充任务输入',
        content: input,
      });
      setInput('');
      await rt.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <h2>动作账本</h2>
      <p className="muted">每个逻辑动作具有稳定身份；重试、原始结果与对账记录单独保留。</p>
      {error && (
        <p className="notice warning" role="alert">
          {error}
        </p>
      )}
      {r.status === 'PAUSED' && (
        <div className="button-row">
          <Button
            onClick={() =>
              setForm({
                title: '准备外部操作',
                path: `/runs/${r.id}/remote-actions`,
                initial: {
                  expected_version: r.stateVersion,
                  reason: '请求审批外部操作',
                  connection_id: '填写任务已选连接 ID',
                  operation: 'tools-name',
                  arguments: {},
                  idempotency_key: crypto.randomUUID(),
                },
              })
            }
          >
            准备外部操作
          </Button>
          <Button
            onClick={() =>
              setForm({
                title: '确认新模型配置',
                path: `/runs/${r.id}/bind-model`,
                initial: {
                  expected_version: r.stateVersion,
                  reason: '确认使用当前服务端已配置模型',
                },
              })
            }
          >
            绑定已配置模型
          </Button>
        </div>
      )}
      {actions.map((a) => (
        <Panel key={a.id} title={`${a.tool} · ${a.status}`}>
          <div className="panel-pad">
            <dl>
              <dt>动作 ID</dt>
              <dd className="mono wrap">{a.id}</dd>
              <dt>效果类型</dt>
              <dd>{a.effect_class}</dd>
              <dt>效果摘要</dt>
              <dd className="mono wrap">{a.effect_digest}</dd>
            </dl>
            <details>
              <summary>参数、尝试与回执</summary>
              <pre className="code-block">{JSON.stringify(a, null, 2)}</pre>
            </details>
            {a.input_deliveries
              ?.filter((d: any) => d.status === 'unknown')
              .map((d: any) => (
                <Button
                  key={d.id}
                  onClick={() =>
                    setForm({
                      title: '核对远程输入投递',
                      path: `/outbox/${d.id}/reconcile`,
                      initial: {
                        expected_version: r.stateVersion,
                        reason: '核查输入投递结果',
                        outcome: 'occurred',
                        evidence: '',
                      },
                    })
                  }
                >
                  核对输入投递
                </Button>
              ))}
            {a.receipt?.remote_status === 'input_required' && (
              <Button
                onClick={() =>
                  setForm({
                    title: '答复远程任务',
                    path: `/actions/${a.id}/input`,
                    initial: {
                      expected_version: r.stateVersion,
                      reason: '用户审查远程输入请求',
                      responses: Object.fromEntries(
                        Object.keys(a.receipt.response?.inputRequests || {}).map((key) => [
                          key,
                          { result: { action: 'accept', content: {} } },
                        ]),
                      ),
                    },
                  })
                }
              >
                审查并答复输入
              </Button>
            )}
            {a.status === 'UNKNOWN' && (
              <>
                <label className="field">
                  已确认结果
                  <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
                    <option value="occurred">远端操作已发生</option>
                    <option value="absent">远端操作未发生</option>
                  </select>
                </label>
                <label className="field">
                  对账证据
                  <textarea
                    value={evidence}
                    onChange={(e) => setEvidence(e.target.value)}
                    placeholder="填写远端回执编号、查询结果或人工核查记录（至少 10 字符）"
                  />
                </label>
                <Button disabled={evidence.trim().length < 10} onClick={() => reconcile(a.id)}>
                  提交对账处置
                </Button>
              </>
            )}
          </div>
        </Panel>
      ))}
      {!actions.length && (
        <Empty title="尚无工具动作" description="模型产生的动作意图会先写入账本，再执行。" />
      )}
      {budget?.resources && (
        <p className="muted">
          Root 资源：{budget.resources.tokens_spent || 0} Tokens 已结算 ·{' '}
          {budget.resources.tokens_reserved || 0} Tokens 预留 · {budget.resources.tool_calls || 0} /{' '}
          {budget.limits?.max_tool_calls} 次工具调用
        </p>
      )}
      {budget && (
        <Panel title="预算账本">
          <div className="panel-pad">
            <p>
              已结算 ${(budget.spent_micros / 1e6).toFixed(6)} · 预留 $
              {(budget.reserved_micros / 1e6).toFixed(6)}
            </p>
            {budget.entries.map((entry: any) => (
              <div className="list-row" key={entry.id}>
                <span className="mono wrap">{entry.operation_id}</span>
                <span>{entry.status}</span>
                {entry.status === 'unknown' && entry.run_id === r.id && (
                  <Button
                    onClick={() =>
                      setForm({
                        title: '模型账单对账',
                        path: `/runs/${r.id}/budget/${entry.operation_id}/reconcile`,
                        initial: {
                          expected_version: r.stateVersion,
                          reason: '核对提供方账单',
                          actual_micros: entry.reserved,
                          evidence: '',
                        },
                      })
                    }
                  >
                    核对账单
                  </Button>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}
      {r.status === 'PAUSED' && (
        <Panel title="补充输入">
          <div className="panel-pad">
            <label className="field">
              补充信息
              <textarea value={input} onChange={(e) => setInput(e.target.value)} />
            </label>
            <Button disabled={!input.trim()} onClick={send}>
              保存输入
            </Button>
            <p className="muted">保存后可在恢复诊断中继续任务。</p>
          </div>
        </Panel>
      )}
      {form && (
        <JsonForm
          {...form}
          onDone={() => {
            setForm(null);
            void rt.refresh();
          }}
        />
      )}
    </>
  );
}

export function RealContext({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const [turn, setTurn] = useState(1);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setError('');
    api(`/runs/${r.id}/context/${turn}`)
      .then(setData)
      .catch((e) => {
        setData(null);
        setError(e.message);
      });
  }, [r.id, turn, r.stateVersion]);
  return (
    <>
      <h2>实际模型上下文</h2>
      <label className="field">
        决策轮次
        <input
          type="number"
          min={1}
          value={turn}
          onChange={(e) => setTurn(Math.max(1, Number(e.target.value)))}
        />
      </label>
      {error && <p className="muted">{error}</p>}
      {data && (
        <>
          <div className="context-summary">
            <div>
              <strong>{data.estimated_tokens_upper_bound}</strong>
              <span>保守 Token 上界</span>
            </div>
            <div>
              <strong>{data.input_budget}</strong>
              <span>输入预算</span>
            </div>
            <div>
              <strong>{data.omitted.length}</strong>
              <span>省略项</span>
            </div>
          </div>
          <p className="muted">固定约束优先；按来源和摘要去重。原始观察仍在证据存储中。</p>
          <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>
          <Button
            onClick={() =>
              rt.app.download(`${r.id}-context-${turn}.json`, JSON.stringify(data, null, 2))
            }
          >
            导出上下文清单
          </Button>
        </>
      )}
    </>
  );
}
