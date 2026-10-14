import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError } from './api';
import { draftKey } from './drafts';
import { Button } from './components';
import { useCatalog } from './useCatalog';
import { useRemote } from './BackendViews';
import { ProjectWizard } from './ProjectWizard';
import {
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  Code2,
  Copy,
  FileCode2,
  FlaskConical,
  ForgeMark,
  GitBranch,
  LoaderCircle,
  Pause,
  Plus,
  Project,
  ShieldCheck,
  Terminal,
  TriangleAlert,
} from './icons';
import type { Runtime } from './useRuntime';
import { runMessage, type Execution } from './runtime';

type Message = { id: string; role: 'user' | 'assistant'; content: string; time: string };
type Conversation = {
  version: number;
  plan_digest: string;
  initial: string;
  messages: Message[];
  plan_ready: boolean;
  execution_id: string | null;
};

function phaseLabel(run: Execution) {
  if (run.user_message || ['SUCCEEDED', 'FAILED', 'CANCELLED', 'PAUSED'].includes(run.status))
    return runMessage(run);
  const labels: Record<string, string> = {
    QUEUED: '等待开始',
    CREATED: '准备任务',
    PLANNING: '正在阅读项目并整理方案',
    EXECUTING: '正在执行修改',
    VERIFYING: '正在运行独立验收',
    RECOVERING: '正在检查故障与恢复条件',
    DISCUSSING: '正在整理回复',
    SUCCEEDED: '已验证完成',
    FAILED: '任务失败，现场已保留',
    CANCELLED: '任务已取消',
  };
  return labels[run.phase] || run.phase;
}

function Composer({
  value,
  onChange,
  onSend,
  busy,
  disabled,
  readOnly,
  label,
  children,
  hint,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: () => void;
  busy: boolean;
  disabled?: boolean;
  readOnly?: boolean;
  label: string;
  children?: ReactNode;
  hint: string;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!field.current) return;
    field.current.style.height = 'auto';
    field.current.style.height = Math.min(240, field.current.scrollHeight) + 'px';
  }, [value]);
  return (
    <form
      className="chat-composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (!disabled && !busy && value.trim()) onSend();
      }}
    >
      <textarea
        ref={field}
        aria-label={label}
        value={value}
        readOnly={readOnly || busy}
        maxLength={10000}
        rows={3}
        placeholder={label}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (!disabled && !busy && value.trim()) onSend();
          }
        }}
      />
      <div className="composer-bottom">
        <div className="composer-options">{children}</div>
        <button
          className="chat-send"
          aria-label="发送消息"
          type="submit"
          disabled={disabled || busy || !value.trim()}
        >
          {busy ? <LoaderCircle size={20} className="chat-spinner" /> : <ArrowUpRight size={21} />}
        </button>
      </div>
      <p className="composer-hint">{hint}</p>
    </form>
  );
}

function savedDraft() {
  try {
    return JSON.parse(localStorage.getItem(draftKey('chat')) || '{}');
  } catch {
    return {};
  }
}

export function StartWork({ runtime: rt }: { runtime: Runtime }) {
  const [draft] = useState(savedDraft);
  const [text, setText] = useState<string>(draft.text || '');
  const [selected, setSelected] = useState<string>(
    rt.route.project === '所有项目' ? draft.project || '' : rt.route.project,
  );
  const [budget, setBudget] = useState<number>(draft.budget || rt.app.settings.budget);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [wizard, setWizard] = useState(false);
  const submitting = useRef(false);
  const projects = useCatalog<{ id: string; name?: string }>('/catalog/projects?limit=100');
  const diagnostics = useRemote('/diagnostics');
  useEffect(() => {
    try {
      localStorage.setItem(draftKey('chat'), JSON.stringify({ text, project: selected, budget }));
    } catch {}
  }, [text, selected, budget]);
  async function start() {
    if (submitting.current || !text.trim()) return;
    if (!selected || !projects.items.some((p) => p.id === selected)) {
      setError('先选择一个项目，或接入你的代码。需求草稿会为你保留。');
      return;
    }
    if (!Number.isFinite(budget) || budget < 0.1 || budget > 100) {
      setError('对话额度应为 0.1–100 USD。');
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      await rt.discuss(text.trim(), selected, budget);
      localStorage.removeItem(draftKey('chat'));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  const examples = [
    {
      icon: Code2,
      title: '修复一个 Bug',
      text: '帮我定位并修复这个项目中的问题：\n\n现象：\n期望行为：\n\n请先分析原因、提出方案，等我确认后再改代码。',
    },
    {
      icon: Plus,
      title: '增加一个功能',
      text: '我想给这个项目增加一个功能：\n\n使用场景：\n希望实现：\n\n请先检查现有实现，和我确认方案及测试方式。',
    },
    {
      icon: GitBranch,
      title: '参考现有实现',
      text: '我想参考另一个项目的实现，为当前项目增加类似功能。\n\n参照项目：\n目标功能：\n\n请先说明需要哪些代码或分析证据，再一起确定实现方案。',
    },
  ];
  return (
    <div className="start-work">
      <section className="chat-welcome" aria-label="开始一项工作">
        <span className="welcome-mark">
          <ForgeMark size={30} />
        </span>
        <p className="welcome-eyebrow">你的项目，从想法到可验证的修改</p>
        <h1>今天想对项目做些什么？</h1>
        <p className="welcome-description">
          说说你的需求。一起理清方案，再修改代码、运行测试，交付你能检查的结果。
        </p>
        <Composer
          value={text}
          onChange={(value) => {
            setText(value);
            setError('');
          }}
          onSend={() => void start()}
          busy={busy}
          label="描述你的需求，或粘贴遇到的问题…"
          hint="Enter 发送 · Shift + Enter 换行 · 先讨论，确认方案后才会修改代码"
        >
          <label className="composer-project">
            <Project size={17} />
            <span className="sr-only">选择工作项目</span>
            <select
              aria-label="选择工作项目"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">选择项目</option>
              {projects.items.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id === 'runtime-lab' ? '示例项目 · Runtime Lab' : p.name || p.id}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="text-action" onClick={() => setWizard(true)}>
            <Plus size={15} />
            接入项目
          </button>
          <details className="chat-budget">
            <summary>对话额度 ${budget}</summary>
            <label>
              本次对话费用上限（USD）
              <input
                type="number"
                min="0.1"
                max="100"
                step="0.1"
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
              />
            </label>
          </details>
        </Composer>
        {error && (
          <p role="alert" className="chat-error">
            {error}
          </p>
        )}
        {projects.error && (
          <p role="alert" className="chat-error">
            项目加载失败：{projects.error}
            <Button onClick={() => void projects.reload()}>重试</Button>
          </p>
        )}
        {projects.cursor && <Button onClick={() => void projects.more()}>加载更多项目</Button>}
        <div className="starter-examples">
          {examples.map(({ icon: Icon, title, text: prompt }) => (
            <button
              key={title}
              onClick={() => {
                setText(prompt);
                document.querySelector<HTMLTextAreaElement>('.chat-composer textarea')?.focus();
              }}
            >
              <Icon size={19} />
              <span>{title}</span>
              <ArrowUpRight size={14} />
            </button>
          ))}
        </div>
        {diagnostics.data && !diagnostics.data.model.configured && (
          <p className="chat-readiness">
            <Terminal size={16} />
            尚未配置模型，可以先体验示例流程。
            <button className="text-action" onClick={() => rt.app.navigate('settings')}>
              查看配置
            </button>
          </p>
        )}
        <div className="welcome-secondary">
          <button className="text-action" onClick={() => void rt.scenario('active')}>
            <FlaskConical size={16} />
            运行契约示例
          </button>
          <span>不调用付费模型，体验执行与交付</span>
          <button className="text-action" aria-label="创建任务" onClick={rt.app.newRun}>
            高级创建任务
          </button>
        </div>
      </section>
      {!!rt.summary?.attention_runs?.length && (
        <section className="home-attention">
          <h2>等你回应</h2>
          {rt.summary.attention_runs.slice(0, 2).map((r: Execution) => (
            <button key={r.id} onClick={() => rt.app.openRun(r.id)}>
              <span>
                <strong>{r.title}</strong>
                <small>{r.mode === 'discuss' ? '继续对话或确认方案' : r.phase}</small>
              </span>
              <ArrowRight size={18} />
            </button>
          ))}
        </section>
      )}
      <section className="recent-work">
        <div className="section-title">
          <h2>继续上次的工作</h2>
          <button className="text-action" onClick={() => rt.app.navigate('runs')}>
            全部任务
            <ArrowRight size={15} />
          </button>
        </div>
        {rt.state.runs.slice(0, 4).map((r) => (
          <button className="recent-work-row" key={r.id} onClick={() => rt.app.openRun(r.id)}>
            <span className="recent-work-icon">
              {r.mode === 'discuss' ? <ForgeMark size={19} /> : <FileCode2 size={19} />}
            </span>
            <span>
              <strong>{r.title}</strong>
              <small>
                {r.project} ·{' '}
                {r.mode === 'discuss'
                  ? r.executionId
                    ? '方案已开始执行'
                    : '需求讨论'
                  : r.status === 'SUCCEEDED'
                    ? '已验证完成，可查看交付'
                    : phaseLabel(r)}
              </small>
            </span>
            <ArrowUpRight size={16} />
          </button>
        ))}
        {!rt.state.runs.length && (
          <p className="muted">你的对话和任务会保存在这里。可以先选择示例项目，说说你想改什么。</p>
        )}
      </section>
      {wizard && (
        <ProjectWizard
          onClose={() => setWizard(false)}
          onDone={() => {
            setWizard(false);
            void projects.reload();
            void rt.refresh();
          }}
        />
      )}
    </div>
  );
}

function inline(text: string) {
  return text
    .split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
    .map((part, i) =>
      part.startsWith('**') ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : part.startsWith('`') ? (
        <code key={i}>{part.slice(1, -1)}</code>
      ) : (
        part
      ),
    );
}

function MessageText({ text }: { text: string }) {
  return (
    <div className="chat-prose">
      {text.split(/```/g).map((block, i) =>
        i % 2 ? (
          <pre key={i}>
            <code>{block.replace(/^[\w+-]*\n/, '')}</code>
          </pre>
        ) : (
          <div key={i}>
            {block.split('\n').map((line, n) =>
              /^#{1,4} /.test(line) ? (
                <h3 key={n}>{inline(line.replace(/^#{1,4} /, ''))}</h3>
              ) : (
                <div key={n} className={/^[-*] /.test(line) ? 'chat-list-item' : 'chat-text-line'}>
                  {inline(line.replace(/^[-*] /, '') || '\u00a0')}
                </div>
              ),
            )}
          </div>
        ),
      )}
    </div>
  );
}

export function ConversationPanel({ runtime: rt, run: r }: { runtime: Runtime; run: Execution }) {
  const remote = useRemote(`/runs/${r.id}/conversation`, 2500);
  const history = remote.data as Conversation | null;
  const storageKey = draftKey(`reply-${r.id}`);
  const [text, setText] = useState(() => {
    try {
      return localStorage.getItem(storageKey) || '';
    } catch {
      return '';
    }
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [followup, setFollowup] = useState(false);
  const [followupBudget, setFollowupBudget] = useState(rt.app.settings.budget);
  const sending = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const endVisible = useRef(true);
  const previousMessageCount = useRef<number | null>(null);
  const [unread, setUnread] = useState(false);
  const pending = useRef<{ content: string; expected_version: number; key: string } | null>(null);
  useEffect(() => {
    try {
      pending.current = JSON.parse(localStorage.getItem(storageKey + '-pending') || 'null');
    } catch {}
  }, [storageKey]);
  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      endVisible.current = entries[0].isIntersecting;
      if (endVisible.current) setUnread(false);
    });
    if (bottom.current) observer.observe(bottom.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!history) return;
    const previous = previousMessageCount.current;
    previousMessageCount.current = history.messages.length;
    if (previous === null || previous >= history.messages.length) return;
    if (endVisible.current)
      bottom.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
    else setUnread(true);
  }, [history?.messages.length]);
  const discussing = r.mode === 'discuss';
  const terminal = ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(r.status);
  const canReply =
    r.status === 'PAUSED' &&
    !r.cancelRequested &&
    !r.unknownEffect &&
    !r.budget_unknown &&
    !r.executionId;
  async function send() {
    if (sending.current || !text.trim()) return;
    sending.current = true;
    setBusy(true);
    setError('');
    if (terminal) {
      try {
        if (!Number.isFinite(followupBudget) || followupBudget < 0.1 || followupBudget > 100)
          throw Error('对话额度应为 0.1–100 USD。');
        await rt.discuss(text.trim(), r.project, followupBudget, r.id);
        localStorage.removeItem(storageKey);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        sending.current = false;
        setBusy(false);
      }
      return;
    }
    const request = pending.current || {
      content: text.trim(),
      expected_version: r.stateVersion || 1,
      key: crypto.randomUUID(),
    };
    pending.current = request;
    try {
      localStorage.setItem(storageKey + '-pending', JSON.stringify(request));
      await api(
        `/runs/${r.id}/messages`,
        'POST',
        { content: request.content, expected_version: request.expected_version },
        request.key,
      );
      pending.current = null;
      localStorage.removeItem(storageKey + '-pending');
      localStorage.removeItem(storageKey);
      setText('');
      await remote.reload();
      await rt.refresh();
    } catch (e) {
      if (e instanceof ApiError && !e.uncertain) {
        pending.current = null;
        localStorage.removeItem(storageKey + '-pending');
      }
      setError((e as Error).message);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  const assistant = history?.messages.filter((m) => m.role === 'assistant').at(-1);
  const summary = rt.state.artifacts.find((a) => a.runId === r.id && a.name === 'summary.md');
  const patch = rt.state.artifacts.find((a) => a.runId === r.id && a.name === 'changes.patch');
  const [deliverySummary, setDeliverySummary] = useState('');
  useEffect(() => {
    let alive = true;
    if (summary)
      void rt
        .artifactText(summary)
        .then((value) => {
          if (alive) setDeliverySummary(value);
        })
        .catch(() => {});
    return () => {
      alive = false;
    };
  }, [summary?.id, summary?.digest]);
  return (
    <div className="conversation-workspace">
      {r.sourceRunId && (
        <div className="chat-context-link">
          <GitBranch size={15} />
          基于上次保留的代码继续讨论
          <button className="text-action" onClick={() => rt.app.openRun(r.sourceRunId!)}>
            查看上次任务
          </button>
        </div>
      )}
      {r.sourceDiscussionId && (
        <div className="chat-context-link">
          <ShieldCheck size={15} />
          按已确认方案执行
          <button className="text-action" onClick={() => rt.app.openRun(r.sourceDiscussionId!)}>
            查看前期讨论
          </button>
        </div>
      )}
      <div className="conversation-messages" aria-label="任务对话">
        <article className="chat-message from-user">
          <span className="sr-only">你</span>
          <div className="user-bubble">{history?.initial || r.description}</div>
        </article>
        {history?.messages.map((m) => (
          <article key={m.id} className={`chat-message from-${m.role}`}>
            {m.role === 'assistant' && (
              <span className="assistant-mark">
                <ForgeMark size={20} />
              </span>
            )}
            <div className={m.role === 'user' ? 'user-bubble' : 'assistant-content'}>
              {m.role === 'assistant' ? (
                <>
                  <span className="chat-author">ForgeAgent</span>
                  <MessageText text={m.content} />
                  <button
                    className="text-action chat-copy"
                    aria-label="复制回复"
                    onClick={() =>
                      void navigator.clipboard
                        .writeText(m.content)
                        .then(() => rt.app.notify('回复已复制'))
                        .catch(() => rt.app.notify('复制失败，请手动选择文本'))
                    }
                  >
                    <Copy size={14} />
                    复制
                  </button>
                </>
              ) : (
                m.content
              )}
            </div>
          </article>
        ))}
        {deliverySummary && (
          <article className="chat-message from-assistant">
            <span className="assistant-mark">
              <ForgeMark size={20} />
            </span>
            <div className="assistant-content">
              <span className="chat-author">ForgeAgent · 交付说明</span>
              <MessageText text={deliverySummary} />
            </div>
          </article>
        )}
      </div>
      {remote.error && (
        <p role="alert" className="chat-error">
          对话加载失败：{remote.error}
          <Button onClick={() => void remote.reload()}>重试</Button>
        </p>
      )}
      {!terminal && !r.executionId && (
        <div className="chat-status" role="status">
          {r.status === 'ACTIVE' || r.status === 'QUEUED' || r.status === 'WAITING' ? (
            <LoaderCircle size={17} className="chat-spinner" />
          ) : (
            <ShieldCheck size={17} />
          )}
          <span>
            {discussing
              ? r.planReady
                ? '方案已整理好，你可以继续讨论，或确认后执行。'
                : r.status === 'PAUSED'
                  ? r.inputRequired
                    ? '等待你的回复。讨论阶段不会修改代码。'
                    : runMessage(r)
                  : '正在阅读项目并整理回复…'
              : phaseLabel(r)}
          </span>
        </div>
      )}
      {discussing && r.planReady && !r.executionId && (
        <div className="chat-plan-confirm">
          <div>
            <strong>接下来，按这个方案执行？</strong>
            <p>你将确认修改范围、验收要求和单独的执行额度。原项目不会被直接覆盖。</p>
          </div>
          <Button
            primary
            disabled={
              !history?.plan_ready ||
              history.version !== r.stateVersion ||
              !assistant ||
              busy ||
              !!pending.current
            }
            onClick={() => history && rt.prepareExecution(r, assistant?.content || '', history)}
          >
            确认方案并执行
            <ArrowRight size={16} />
          </Button>
        </div>
      )}
      {r.executionId && (
        <div className="chat-plan-confirm">
          <div>
            <strong>方案已开始执行</strong>
            <p>此对话保留为方案记录，后续进展和补充说明在执行任务中处理。</p>
          </div>
          <Button primary onClick={() => rt.app.openRun(r.executionId!)}>
            查看执行任务
            <ArrowRight size={16} />
          </Button>
        </div>
      )}
      {terminal && (
        <div className={`chat-delivery ${r.status === 'SUCCEEDED' ? '' : 'delivery-incomplete'}`}>
          {r.status === 'SUCCEEDED' ? <CheckCircle2 size={22} /> : <TriangleAlert size={22} />}
          <div>
            <strong>
              {r.status === 'SUCCEEDED'
                ? '修改已通过独立验收'
                : '本次任务已结束，现场和已有产物已保留'}
            </strong>
            <p>
              {r.status === 'SUCCEEDED'
                ? '查看代码补丁与测试结果，再决定如何应用到你的项目。'
                : phaseLabel(r)}
            </p>
          </div>
          <div className="button-row">
            <Button primary onClick={() => rt.setTab('changes')}>
              查看代码变更
            </Button>
            <Button onClick={() => rt.setTab('verification')}>查看测试结果</Button>
            {patch && <Button onClick={() => void rt.downloadArtifact(patch)}>下载补丁</Button>}
            {!r.unknownEffect && !r.budget_unknown && (
              <Button onClick={() => setFollowup(true)}>继续讨论修改</Button>
            )}
          </div>
        </div>
      )}
      {unread && (
        <button
          className="chat-new-message text-action"
          onClick={() => {
            bottom.current?.scrollIntoView({ block: 'nearest' });
            setUnread(false);
          }}
        >
          有新回复，跳到最新消息
        </button>
      )}
      {((!terminal && !r.executionId) || (terminal && followup)) && (
        <div className="conversation-input">
          <Composer
            value={text}
            onChange={(value) => {
              if (!pending.current) {
                setText(value);
                try {
                  localStorage.setItem(storageKey, value);
                } catch {}
              }
            }}
            onSend={() => void send()}
            busy={busy}
            readOnly={!!pending.current}
            disabled={!terminal && !canReply && !pending.current}
            label={
              terminal
                ? '基于这次结果，你还想修改什么？'
                : discussing
                  ? '继续讨论你的需求…'
                  : '补充说明或回复 Agent 的问题…'
            }
            hint={
              terminal
                ? '将保留现有代码，开启新一轮只读讨论；新方案确认后再执行，对话费用单独记录。'
                : pending.current
                  ? '上次发送结果尚未确认。再次发送将核对同一条消息，避免重复提交。'
                  : canReply
                    ? 'Enter 发送 · Shift + Enter 换行 · 补充说明不会扩大已确认的权限和验收范围'
                    : r.unknownEffect || r.budget_unknown
                      ? '先在执行详情中确认未结算的操作或费用，再继续对话'
                      : '执行中可先写下补充说明，暂停后发送'
            }
          >
            <span className="composer-mode">
              <ForgeMark size={16} />
              {discussing || terminal ? '只读讨论' : '任务协作'}
            </span>
            {terminal && (
              <details className="chat-budget">
                <summary>对话额度 ${followupBudget}</summary>
                <label>
                  本次对话费用上限（USD）
                  <input
                    type="number"
                    min="0.1"
                    max="100"
                    step="0.1"
                    value={followupBudget}
                    onChange={(e) => setFollowupBudget(Number(e.target.value))}
                  />
                </label>
              </details>
            )}
            {!canReply && r.status === 'ACTIVE' && !r.cancelRequested && (
              <button
                type="button"
                className="text-action"
                onClick={() => rt.app.updateRun(r.id, 'pause')}
              >
                <Pause size={15} />
                暂停并补充
              </button>
            )}
          </Composer>
          {error && (
            <p className="chat-error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
      <div ref={bottom} className="chat-bottom-anchor" />
    </div>
  );
}
