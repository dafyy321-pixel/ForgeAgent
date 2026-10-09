import { useEffect, useRef, useState } from 'react';
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Bookmark,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Copy,
  FileCode2,
  FileText,
  GitBranch,
  Info,
  Layers3,
  LoaderCircle,
  MoreHorizontal,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Square,
  Terminal,
  TriangleAlert,
  XCircle,
} from './icons';
import { Button, Dialog, Empty } from './components';
import {
  checksFor,
  needsAttention,
  relativeTime,
  waitLabels,
  type Decision,
  type Execution,
} from './runtime';
import type { Runtime } from './useRuntime';
import { EventStream, RunBadge, useFollowing } from './TaskViews';
import { ActionLedger, RealContext } from './RuntimePanels';

export function TaskWorkspace({ runtime: rt }: { runtime: Runtime }) {
  const r = rt.state.runs.find((r) => r.id === rt.route.id);
  if (rt.loading) return <p role="status">正在从服务端加载任务…</p>;
  if (!r)
    return (
      <>
        <button className="text-action" onClick={rt.back}>
          <ArrowLeft size={16} />
          返回任务
        </button>
        <h1>任务不可用</h1>
        <Empty title="当前工作空间没有这条任务" description="请检查任务地址和当前登录身份。">
          <Button primary onClick={() => rt.app.navigate('runs')}>
            查看当前任务
          </Button>
        </Empty>
      </>
    );
  return <RunWorkspace key={r.id} runtime={rt} run={r} />;
}
function RunWorkspace({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const { saved, toggle } = useFollowing();
  const [cancel, setCancel] = useState(false);
  const [link, setLink] = useState('');
  const [copyError, setCopyError] = useState('');
  const tab = rt.route.tab;
  const approvals = rt.state.approvals.filter((a) => a.runId === r.id);
  const tabs = [
    ['timeline', '执行记录'],
    ['actions', '动作账本'],
    ['changes', '变更与产物'],
    ['verification', '验收结果'],
    ...(approvals.length ? [['review', '审查操作']] : []),
    ['recovery', '恢复诊断'],
    ['context', '上下文'],
  ];
  const active = r.status === 'ACTIVE';
  const verifying = r.verification.status === 'running';
  async function share() {
    const url = `${location.origin}/tasks/${encodeURIComponent(r.id)}`;
    setLink(url);
    try {
      await navigator.clipboard.writeText(url);
      rt.app.notify('任务链接已复制');
    } catch {
      setCopyError('浏览器未允许复制，请选中下方链接手动复制。');
    }
  }
  return (
    <div className="task-workspace">
      <button className="back-button" onClick={rt.back}>
        <ArrowLeft size={17} />
        返回任务列表
      </button>
      <div className="task-heading">
        <div className="task-heading-meta">
          <span>
            <GitBranch size={15} />
            {r.project}
          </span>
          <span className="mono">{r.id}</span>
          <span>产物 v{r.version}</span>
        </div>
        <h1>{r.title}</h1>
        <div className="task-heading-bottom">
          <div className="button-row">
            <RunBadge run={r} />
            <span className="small-note">{relativeTime(r.updatedAt)}更新</span>
          </div>
          <div className="button-row">
            <button
              className={`icon-btn ${saved.includes(r.id) ? 'selected' : ''}`}
              aria-label={saved.includes(r.id) ? '取消关注任务' : '关注任务'}
              aria-pressed={saved.includes(r.id)}
              onClick={() => toggle(r.id)}
            >
              <Bookmark size={17} fill={saved.includes(r.id) ? 'currentColor' : 'none'} />
            </button>
            <button className="icon-btn" aria-label="复制任务链接" onClick={share}>
              <Copy size={17} />
            </button>
            <details className="more-menu">
              <summary aria-label="更多任务操作">
                <MoreHorizontal size={20} />
              </summary>
              <div>
                <button
                  onClick={() =>
                    rt.app.download(
                      `${r.id}-events.json`,
                      JSON.stringify(
                        {
                          runtime: true,
                          run: r,
                          events: rt.state.events.filter((e) => e.runId === r.id),
                        },
                        null,
                        2,
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={15} />
                  导出任务记录
                </button>
                {!['SUCCEEDED', 'CANCELLED'].includes(r.status) && !r.cancelRequested && (
                  <button className="danger-text" onClick={() => setCancel(true)}>
                    <Square size={15} />
                    取消任务
                  </button>
                )}
              </div>
            </details>
            {active && !verifying && (
              <Button onClick={() => rt.app.updateRun(r.id, 'pause')}>
                <Pause size={15} />
                请求暂停
              </Button>
            )}
            {r.status === 'SUCCEEDED' && (
              <Button primary onClick={() => rt.setTab('verification')}>
                查看交付
                <ArrowRight size={15} />
              </Button>
            )}
          </div>
        </div>
      </div>
      {(r.waitReason || r.cancelRequested || ['PAUSED', 'FAILED', 'QUEUED'].includes(r.status)) && (
        <div className={`state-banner ${r.status === 'FAILED' ? 'error' : ''}`}>
          <span>
            {r.status === 'FAILED' ? (
              <XCircle size={20} />
            ) : r.cancelRequested ? (
              <Clock3 size={20} />
            ) : (
              <Info size={20} />
            )}
          </span>
          <div>
            <strong>{r.waitReason ? waitLabels[r.waitReason] : r.phase}</strong>
            <p>
              {r.cancelRequested
                ? '取消不会撤销已发生的外部变更。未完成效果确认前，任务不会恢复或重发。'
                : r.waitReason === 'APPROVAL'
                  ? '请审查明确的目标与影响，授权后才能继续。'
                  : r.unknownEffect
                    ? '远端响应丢失，不代表操作失败。请先确认结果。'
                    : r.status === 'QUEUED'
                      ? '等待隔离执行资源，不会提前派发工具操作。'
                      : r.phase}
            </p>
          </div>
          {needsAttention(r) && (
            <Button onClick={() => rt.setTab(r.waitReason === 'APPROVAL' ? 'review' : 'recovery')}>
              {r.waitReason === 'APPROVAL' ? '审查操作' : '检查与处理'}
              <ArrowRight size={15} />
            </Button>
          )}
        </div>
      )}
      <div className="milestones" aria-label="任务里程碑">
        {r.milestones.map((m, i) => (
          <div className={m.done ? 'done' : ''} key={m.label}>
            <span>{m.done ? <Check size={13} /> : i + 1}</span>
            {m.label}
          </div>
        ))}
      </div>
      <nav className="task-tabs" aria-label="任务工作区">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? 'active' : ''}
            aria-current={tab === id ? 'page' : undefined}
            onClick={() => rt.setTab(id)}
          >
            {label}
            {id === 'review' && approvals.some((a) => a.status === 'pending') && <i />}
          </button>
        ))}
      </nav>
      {tab === 'timeline' && (
        <>
          <div className="task-contract">
            <h2>任务契约</h2>
            <p>{r.description}</p>
            <dl>
              <dt>允许修改</dt>
              <dd>{r.scope}</dd>
              <dt>验收条件</dt>
              <dd>{r.criteria.join('；')}</dd>
            </dl>
          </div>
          <div className="section-title">
            <h2>执行记录</h2>
            <span className="small-note">结构化事件 · 原始观察可展开</span>
          </div>
          <EventStream runtime={rt} events={rt.state.events.filter((e) => e.runId === r.id)} />
        </>
      )}
      {tab === 'actions' && <ActionLedger runtime={rt} run={r} />}
      {tab === 'review' && (
        <ApprovalReview key={approvals.map((a) => a.id).join(',')} runtime={rt} run={r} />
      )}
      {tab === 'recovery' && <RecoveryPanel runtime={rt} run={r} />}
      {tab === 'changes' && <EvidencePanel runtime={rt} run={r} />}
      {tab === 'verification' && <VerificationPanel runtime={rt} run={r} />}
      {tab === 'context' && <RealContext runtime={rt} run={r} />}
      {!tabs.some(([id]) => id === tab) && (
        <Empty title="此任务页面不存在" description="请从上方选择有效的任务页面。">
          <Button onClick={() => rt.setTab('timeline')}>执行记录</Button>
        </Empty>
      )}
      {cancel && (
        <Dialog title="确认取消任务？" onClose={() => setCancel(false)}>
          <p>停止派发新动作，并等待在途操作结算。已经发布的代码或其他外部变更不会自动撤销。</p>
          {r.unknownEffect && (
            <div className="notice warning">当前有结果待确认的外部操作，取消会保留待处理事项。</div>
          )}
          <div className="form-footer">
            <Button onClick={() => setCancel(false)}>保留任务</Button>
            <Button
              danger
              onClick={() => {
                rt.app.updateRun(r.id, 'cancel');
                setCancel(false);
              }}
            >
              确认取消
            </Button>
          </div>
        </Dialog>
      )}
      {link && (
        <Dialog
          title="任务链接"
          onClose={() => {
            setLink('');
            setCopyError('');
          }}
        >
          <p className="muted">
            任务保存在服务端。其他设备使用同一服务和有权限的身份即可打开；链接本身不会授予访问权限。
          </p>
          <input
            className="share-input"
            aria-label="任务链接"
            readOnly
            value={link}
            onFocus={(e) => e.target.select()}
          />
          {copyError && (
            <p role="alert" className="field-error">
              {copyError}
            </p>
          )}
          <div className="form-footer">
            <Button onClick={() => setLink('')}>关闭</Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
function ApprovalReview({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const all = rt.state.approvals.filter((a) => a.runId === r.id);
  const [id, setId] = useState(all.find((a) => a.status === 'pending')?.id || all[0]?.id || '');
  const a = all.find((a) => a.id === id) || all[0];
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const busy = !!a?.submission;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  if (!a)
    return (
      <Empty
        title="当前任务没有授权请求"
        description="需要执行受控外部操作时，具体请求会显示在这里。"
      />
    );
  const valid =
    a.status === 'pending' && a.expiresAt > rt.now && a.version === r.version && !r.cancelRequested;
  const submit = async (decision: 'approved' | 'denied') => {
    if (busy) return;
    if (decision === 'denied' && !reason.trim()) {
      setError('请填写拒绝原因。');
      return;
    }
    setError((await rt.decide(a.id, decision, reason)) || '');
  };
  return (
    <div className="review-workspace">
      <div className="section-title">
        <h2>操作影响审查</h2>
        <span className={`decision-status ${a.status}`}>
          {
            { pending: '等待审查', approved: '已批准', denied: '已拒绝', expired: '已失效' }[
              a.status
            ]
          }
        </span>
      </div>
      {all.length > 1 && (
        <label className="field">
          选择审批记录
          <select
            value={a.id}
            onChange={(e) => {
              setId(e.target.value);
              setReason('');
              setError('');
            }}
          >
            {all.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title} · v{a.version} · {a.status}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="review-summary">
        <ShieldCheck size={24} />
        <div>
          <h3>{a.title}</h3>
          <p>{a.impact}</p>
        </div>
      </div>
      <dl className="review-facts">
        <div>
          <dt>目标仓库 / 资源</dt>
          <dd>{a.target}</dd>
        </div>
        <div>
          <dt>影响范围</dt>
          <dd>{a.risk} · 单次操作</dd>
        </div>
        <div>
          <dt>审查版本</dt>
          <dd>
            v{a.version}{' '}
            {a.version !== r.version && (
              <span className="danger-text">（当前为 v{r.version}）</span>
            )}
          </dd>
        </div>
        <div>
          <dt>授权有效期</dt>
          <dd>
            {a.status !== 'pending'
              ? '此记录已结束'
              : valid
                ? `剩余 ${Math.max(0, Math.ceil((a.expiresAt - rt.now) / 60000))} 分钟`
                : '已失效'}
          </dd>
        </div>
      </dl>
      <div className="section-title">
        <h2>{a.tool.includes('network') ? '网络访问变更' : '拟发布的代码变更'}</h2>
        <span className="small-note">来源：当前授权操作</span>
      </div>
      <Diff content={a.diff} />
      <details className="technical-details">
        <summary>授权技术详情</summary>
        <pre className="code-block">
          {JSON.stringify(
            {
              tool: a.tool,
              target: a.target,
              operation_version: a.version,
              digest: a.digest,
              scope: 'single_action',
              runtime: true,
            },
            null,
            2,
          )}
        </pre>
      </details>
      {a.reason && (
        <div className="notice warning">
          <Info size={17} />
          <span>处理说明：{a.reason}</span>
        </div>
      )}
      {valid && (
        <label className="field">
          决定说明（拒绝时必填）
          <textarea
            value={reason}
            maxLength={1000}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例如：请先补充组件兼容性测试，再重新发起审查。"
          />
        </label>
      )}
      {error && (
        <div className="notice warning" role="alert">
          {error}
        </div>
      )}
      <div className="review-actions">
        {valid ? (
          <>
            <span className="small-note">批准仅针对当前操作与目标版本。</span>
            <div className="button-row">
              <Button danger disabled={busy} onClick={() => submit('denied')}>
                拒绝操作
              </Button>
              <Button primary disabled={busy} onClick={() => submit('approved')}>
                {busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}
                批准当前操作
              </Button>
            </div>
          </>
        ) : (
          <>
            <span className="small-note">
              {a.status === 'approved' ? '授权决定已记录。' : '旧授权不会继续用于当前操作。'}
            </span>
            {!r.cancelRequested &&
              !['SUCCEEDED', 'CANCELLED'].includes(r.status) &&
              a.status !== 'approved' && (
                <Button onClick={() => rt.requestApproval(r.id)}>重新发起当前版本审查</Button>
              )}
          </>
        )}
      </div>
    </div>
  );
}
function RecoveryPanel({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const checks = checksFor(r, rt.state.approvals);
  const [choice, setChoice] = useState('');
  const [confirm, setConfirm] = useState(false);
  const busy = !!r.recoveryPending;
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const allowed = r.status === 'PAUSED' && !r.cancelRequested && checks.every((c) => c.ok);
  function recover() {
    setError('');
    rt.app.updateRun(r.id, 'recover');
  }
  return (
    <>
      <div className="section-title">
        <h2>恢复前检查</h2>
        <span className="small-note">
          {checks.filter((c) => c.ok).length} / {checks.length} 条件满足
        </span>
      </div>
      <p className="muted">
        恢复 {r.checkpoint.id}，复用已经确认的动作；原始记录和固定执行配置始终保留。
      </p>
      <div className="recovery-checks">
        {checks.map((c) => (
          <div className={`recovery-check ${c.ok ? 'ok' : 'blocked'}`} key={c.name}>
            {c.ok ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}
            <div>
              <strong>{c.name}</strong>
              <p>{c.detail}</p>
            </div>
            <span>{c.ok ? '通过' : '阻塞'}</span>
          </div>
        ))}
      </div>
      {!r.checkpoint.environment && (
        <div className="repair-action">
          <p>沙箱环境不可用，修复后才能恢复。</p>
          <Button onClick={() => rt.repair(r.id, 'environment')}>重新检查执行环境</Button>
        </div>
      )}
      {!r.checkpoint.compatible && (
        <div className="repair-action">
          <p>执行语义版本不兼容。</p>
          <Button onClick={() => rt.repair(r.id, 'version')}>按当前版本创建新尝试</Button>
        </div>
      )}
      {!r.checkpoint.permission && (
        <Button onClick={() => rt.repair(r.id, 'permission')}>检查当前权限</Button>
      )}

      {error && (
        <div role="alert" className="notice warning">
          {error}
        </div>
      )}
      <div className="review-actions">
        {r.status === 'FAILED' && <Button onClick={() => rt.fork(r.id)}>创建新的尝试</Button>}
        {r.unknownEffect && <Button onClick={() => rt.setTab('actions')}>查看待对账动作</Button>}
        <span className="small-note">
          {r.cancelRequested
            ? '任务已请求取消，不允许重新执行。'
            : allowed
              ? '所有恢复前置条件已满足。'
              : '只有暂停且检查通过的任务才能恢复；终态任务需创建新尝试。'}
        </span>
        <Button primary disabled={!allowed || busy} onClick={recover}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <RotateCcw size={16} />}
          检查并继续任务
        </Button>
      </div>
      {r.status === 'ACTIVE' && (
        <div className="notice success">
          <CheckCircle2 size={18} />
          任务正在运行，当前无需恢复。
        </div>
      )}
      {confirm && (
        <Dialog title="确认记录对账结果？" onClose={() => setConfirm(false)}>
          <p>
            {choice === 'occurred'
              ? '远端操作已经发生，保留回执并禁止重复执行。'
              : '远端操作没有发生，记录确认结果；不会自动重新派发。'}
          </p>
          <p className="muted">请在动作账本提交真实的对账证据。</p>
          <div className="form-footer">
            <Button onClick={() => setConfirm(false)}>返回核对</Button>
            <Button
              primary
              onClick={() => {
                rt.reconcile(r.id, choice === 'occurred');
                setConfirm(false);
              }}
            >
              确认对账
            </Button>
          </div>
        </Dialog>
      )}
    </>
  );
}
export function Diff({ content }: { content: string }) {
  return (
    <div className="diff-view" role="region" aria-label="变更预览">
      <div className="diff-caption">
        <FileCode2 size={16} />
        变更预览 <span>单栏 · 支持长行换行</span>
      </div>
      <pre>
        {content.split('\n').map((line, i) => (
          <div
            className={
              line.startsWith('+') && !line.startsWith('+++')
                ? 'added'
                : line.startsWith('-') && !line.startsWith('---')
                  ? 'removed'
                  : line.startsWith('@@')
                    ? 'hunk'
                    : ''
            }
            key={i}
          >
            <span className="line-number" aria-hidden="true">
              {i + 1}
            </span>
            <code>{line || ' '}</code>
          </div>
        ))}
      </pre>
    </div>
  );
}
function EvidencePanel({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const items = rt.state.artifacts.filter((a) => a.runId === r.id);
  const [selected, setSelected] = useState(items[0]?.id || '');
  const a = items.find((a) => a.id === selected) || items[0];
  const [preview, setPreview] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    setPreview('');
    setError('');
    if (a)
      void rt
        .artifactText(a)
        .then((value) => {
          if (alive) setPreview(value);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    return () => {
      alive = false;
    };
  }, [a?.id, a?.digest]);
  if (!a)
    return (
      <Empty title="尚未生成交付物" description="执行完成后，进入独立验证生成产物与凭证。">
        <Button onClick={() => rt.setTab('verification')}>查看验收条件</Button>
      </Empty>
    );
  return (
    <>
      <div className="section-title">
        <h2>变更与产物</h2>
        <span className="small-note">{items.length} 个文件</span>
      </div>
      <div className="artifact-selector">
        {items.map((a) => (
          <button
            key={a.id}
            className={a.id === selected ? 'selected' : ''}
            onClick={() => setSelected(a.id)}
          >
            <FileText size={17} />
            <span>{a.name}</span>
            <small>v{a.version}</small>
          </button>
        ))}
      </div>
      <div className="artifact-heading">
        <div>
          <h3>{a.name}</h3>
          <span className="small-note">
            {a.type} · v{a.version} ·{' '}
            {a.verified && a.version === r.version ? '验证凭证有效' : '未验证或凭证已失效'}
          </span>
        </div>
        <Button onClick={() => void rt.downloadArtifact(a)}>
          <ArrowDownToLine size={16} />
          下载文件
        </Button>
      </div>
      {a.type === '代码补丁' ? (
        <Diff content={preview} />
      ) : (
        <pre className="code-block artifact-code">{preview}</pre>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="notice">
        <ShieldCheck size={18} />
        <span>
          产物标识：{a.digest}
          <br />
          凭证仅对当前版本有效；服务端使用 SHA-256 校验内容。
        </span>
      </div>
    </>
  );
}
function VerificationPanel({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const verifying = r.verification.status === 'running';
  const valid = r.verification.status === 'passed' && r.verification.version === r.version;
  const items = rt.state.artifacts.filter((a) => a.runId === r.id);
  const allowed = r.status === 'ACTIVE' && !r.cancelRequested && !r.unknownEffect && !verifying;
  const labels = { passed: '通过', failed: '失败', not_run: '未运行', not_applicable: '不适用' };
  return (
    <>
      <div
        className={`verification-banner ${valid ? 'passed' : r.verification.status === 'failed' ? 'failed' : ''}`}
      >
        {valid ? (
          <CheckCircle2 size={29} />
        ) : verifying ? (
          <LoaderCircle size={29} className="spin" />
        ) : (
          <ShieldCheck size={29} />
        )}
        <div>
          <h2>
            {valid
              ? '验收通过，可交付'
              : verifying
                ? '正在独立验证'
                : r.verification.status === 'failed'
                  ? '验收未通过，交付已阻止'
                  : r.verification.status === 'inconclusive'
                    ? '执行环境不足，验收不可判定'
                    : '等待独立验证'}
          </h2>
          <p>
            当前产物 v{r.version} ·{' '}
            {valid
              ? '所有必需检查均通过'
              : verifying
                ? '执行任务约束、回归测试与版本绑定检查'
                : '生成文件不等于完成任务'}
          </p>
        </div>
      </div>
      <div className="section-title">
        <h2>验收清单</h2>
        <span className="small-note">固定于任务创建时</span>
      </div>
      <div className="verification-checks">
        {r.verification.checks.map((c, i) => (
          <div className={`verification-check ${verifying ? 'not_run' : c.status}`} key={i}>
            {verifying ? (
              <LoaderCircle size={19} className="spin" />
            ) : c.status === 'passed' ? (
              <CheckCircle2 size={19} />
            ) : c.status === 'failed' ? (
              <XCircle size={19} />
            ) : (
              <Clock3 size={19} />
            )}
            <div>
              <strong>{c.name}</strong>
              <p>{verifying ? '检查进行中…' : c.evidence}</p>
            </div>
            <span>
              {verifying
                ? '检查中'
                : r.verification.status === 'not_run'
                  ? '未运行'
                  : labels[c.status]}
            </span>
          </div>
        ))}
      </div>
      {r.verification.status === 'failed' && (
        <div className="notice warning" role="alert">
          <TriangleAlert size={18} />
          <span>失败原因已保留在执行事件与验收记录中。请检查与恢复任务，再次执行验证。</span>
          <Button onClick={() => rt.setTab('recovery')}>诊断与恢复</Button>
        </div>
      )}
      {!valid && (
        <div className="review-actions">
          <span className="small-note">
            {allowed
              ? '独立验证器检查当前版本产物。'
              : '仅运行中的任务可发起验证；阻塞事项必须先处理。'}
          </span>
          <Button primary disabled={!allowed} onClick={() => rt.verify(r.id, true)}>
            {verifying ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}
            运行独立验证
          </Button>
        </div>
      )}

      {items.length > 0 && (
        <>
          <div className="section-title">
            <h2>关联交付物与凭证</h2>
          </div>
          {items.map((a) => (
            <div className="evidence-row" key={a.id}>
              <FileText size={19} />
              <div>
                <strong>{a.name}</strong>
                <small>
                  v{a.version} ·{' '}
                  {a.verified && a.version === r.version ? '验证有效' : '不可作为完成凭证'}
                </small>
              </div>
              <Button onClick={() => void rt.downloadArtifact(a)}>
                <ArrowDownToLine size={15} />
                下载
              </Button>
            </div>
          ))}
          <Button onClick={() => rt.setTab('changes')}>
            打开文件预览
            <ArrowRight size={16} />
          </Button>
        </>
      )}
    </>
  );
}
