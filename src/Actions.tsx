import { useEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronRight, Plus, Search, SlidersHorizontal } from './icons';
import { Button, Dialog, Empty } from './components';
import { pageLabels, type Page } from './types';
import type { Execution } from './runtime';
import { useCatalog } from './useCatalog';
import { taskPaths, type Runtime, type TaskDraft } from './useRuntime';
import { useRemote } from './BackendViews';
import { draftKey } from './drafts';

export function ActionOverlays({ runtime: rt }: { runtime: Runtime }) {
  const close = () => rt.setOverlay(null);
  if (rt.overlay === 'create') return <CreateTask runtime={rt} close={close} />;
  if (rt.overlay === 'command') return <CommandSearch runtime={rt} close={close} />;
  if (rt.overlay === 'notifications') return <Notifications runtime={rt} close={close} />;
  return null;
}
function Notifications({ runtime: rt, close }: { runtime: Runtime; close: () => void }) {
  const query = useCatalog<Execution>(
    '/runs?status=attention&limit=50' +
      (rt.route.project === '所有项目' ? '' : '&project=' + encodeURIComponent(rt.route.project)),
  );
  return (
    <Dialog title="待办通知" onClose={close}>
      <p className="muted">当前项目范围内，需要你介入的任务。</p>
      {query.items.map((r) => (
        <button key={r.id} className="command-result" onClick={() => rt.app.openRun(r.id)}>
          <span>
            <strong>{r.title}</strong>
            <small className="cell-sub">{r.phase}</small>
          </span>
          <ChevronRight size={17} />
        </button>
      ))}
      {query.error && <p role="alert">{query.error}</p>}
      {query.cursor && <Button onClick={() => void query.more()}>加载更多待办</Button>}
      <div className="form-footer">
        <Button onClick={() => rt.app.navigate('inbox')}>
          打开待我处理
          <ArrowRight size={16} />
        </Button>
      </div>
    </Dialog>
  );
}
function planFiles(plan: string) {
  const prose = plan.replace(/```[\s\S]*?(?:```|$)/g, ' ');
  const paths = new Set<string>();
  // Only suggest literal relative file names. URLs and code examples do not grant access.
  const tokens = prose.matchAll(/`([^`\n]+)`|([^\s`"'()[\]{}，。；：！？、]+)/g);
  for (const [, quoted, plain] of tokens) {
    const path = (quoted || plain.replace(/[.,;:!?]+$/, '')).replace(/^\.\//, '');
    if (!quoted && !path.includes('/')) continue;
    if (!/^(?:[\p{L}\p{N}_.@+-]+\/)*[\p{L}\p{N}_.@+-]+\.[\w-]+$/u.test(path)) continue;
    if (!path.split('/').some((part: string) => part === '.' || part === '..')) paths.add(path);
  }
  return [...paths];
}

function CreateTask({ runtime: rt, close }: { runtime: Runtime; close: () => void }) {
  const projects = useCatalog<{ id: string }>('/catalog/projects?limit=100');
  const connections = useRemote('/connections');
  const key = draftKey('task');
  const [draft, setDraft] = useState<TaskDraft>(() => {
    if (rt.createContext) return rt.createContext.draft;
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null');
      if (saved) return saved;
    } catch {}
    return {
      title: '',
      description: '',
      criteria: '',
      scope: '',
      project:
        rt.route.project === '所有项目'
          ? rt.state.projects?.[0]?.id || 'runtime-lab'
          : rt.route.project,
      model: 'configured',
      allowedPaths: 'src,tests',
      budget: rt.app.settings.budget,
      skills: rt.state.skills.filter((s) => s.enabled).map((s) => s.id),
    };
  });
  const references = useCatalog<{ id: string; label: string; snapshot_digest: string }>(
    `/reference-targets?project_id=${encodeURIComponent(draft.project)}&limit=50`,
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [advanced, setAdvanced] = useState(false);
  const [limitedScope, setLimitedScope] = useState(false);
  const [busy, setBusy] = useState(false);
  const paths = taskPaths(draft.allowedPaths ?? 'src,tests', 'write');
  const mentioned = planFiles(rt.createContext?.plan || '');
  const uncovered = mentioned.filter(
    (file) => !paths.some((p) => p === file || file.startsWith(p + '/')),
  );
  const submitted = useRef(false);
  const stalePlan =
    !!rt.createContext &&
    rt.state.runs.find((r) => r.id === rt.createContext?.runId)?.stateVersion !==
      rt.createContext.version;
  useEffect(() => {
    if (!submitted.current && !rt.createContext)
      try {
        localStorage.setItem(key, JSON.stringify(draft));
      } catch {}
  }, [draft]);
  const field = <K extends keyof TaskDraft>(key: K, value: TaskDraft[K]) => {
    if (key === 'allowedPaths') setLimitedScope(false);
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => ({ ...e, [key]: '' }));
  };
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (submitted.current || stalePlan) return;
    const next: Record<string, string> = {};
    for (const k of ['title', 'description'] as const)
      if (!draft[k].trim()) next[k] = '请填写此项，任务需要明确的目标与边界。';
    const files = taskPaths(draft.requiredFiles || '', 'file');
    const invalidPath = (path: string) =>
      path.includes(':') || path.split('/').some((part) => !part || part === '.' || part === '..');
    if (!paths.length) next.allowedPaths = '请填写允许写入的文件或目录。';
    else if (paths.length > 100 || paths.some(invalidPath))
      next.allowedPaths = '最多填写 100 项，请使用从项目根目录开始的相对路径。';
    else if (uncovered.length && !limitedScope)
      next.allowedPaths = '请核对方案中其余文件，选择允许写入或确认仅供读取。';
    if (files.length > 100 || files.some((path) => invalidPath(path) || path.length > 500))
      next.requiredFiles = '最多填写 100 个相对文件路径，每项不超过 500 字符。';
    if (draft.criteria.split('\n').filter((s) => s.trim()).length > 30)
      next.criteria = '最多填写 30 项人工验收条件。';
    if (!Number.isFinite(draft.budget) || draft.budget < 0.1 || draft.budget > 100)
      next.budget = '预算应为 0.1–100 USD。';
    setErrors(next);
    if (Object.keys(next).length) {
      if (next.budget) setAdvanced(true);
      return;
    }
    submitted.current = true;
    setBusy(true);
    try {
      await rt.create({ ...draft, title: draft.title.trim() });
      localStorage.removeItem(key);
    } catch (e) {
      submitted.current = false;
      setBusy(false);
      setErrors({ submit: (e as Error).message });
    }
  }
  return (
    <Dialog
      title={rt.createContext ? '确认方案并执行' : '创建任务'}
      subtitle={
        rt.createContext
          ? '确认本次修改范围、验收要求和执行额度。对话费用单独记录，代码在独立工作区修改。'
          : '明确目标、验收条件和允许的修改范围。'
      }
      onClose={() => {
        if (!busy) close();
      }}
      wide
    >
      {rt.createContext && (
        <section className="confirmed-plan-preview">
          <h3>待执行方案</h3>
          <p>{rt.createContext.plan}</p>
          {stalePlan && (
            <div className="notice warning" role="alert">
              方案已更新，请关闭弹窗并重新阅读后确认。<Button onClick={close}>重新阅读方案</Button>
            </div>
          )}
        </section>
      )}
      <form onSubmit={submit} noValidate>
        <label className="field">
          任务目标
          <input
            aria-label="任务目标"
            aria-required="true"
            data-dialog-focus
            maxLength={120}
            value={draft.title}
            onChange={(e) => field('title', e.target.value)}
            placeholder="例如：修复认证模块的并发刷新问题"
            aria-invalid={!!errors.title}
          />
          {errors.title && (
            <span className="field-error" role="alert">
              {errors.title}
            </span>
          )}
        </label>
        <label className="field">
          项目
          <select
            aria-label="项目"
            disabled={!!rt.createContext}
            value={draft.project}
            onChange={(e) => {
              field('project', e.target.value);
              field('referenceTargets', []);
            }}
          >
            {Array.from(
              new Set([
                draft.project,
                ...projects.items.map((p) => p.id),
                ...rt.state.runs.map((r) => r.project),
              ]),
            ).map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        {projects.cursor && <Button onClick={() => void projects.more()}>加载更多项目</Button>}
        <label className="field">
          任务说明
          <textarea
            aria-label="任务说明"
            aria-required="true"
            value={draft.description}
            maxLength={3000}
            onChange={(e) => field('description', e.target.value)}
            placeholder="描述问题背景、期望行为与相关线索…"
            aria-invalid={!!errors.description}
          />
          {errors.description && (
            <span className="field-error" role="alert">
              {errors.description}
            </span>
          )}
        </label>
        <label className="field">
          允许写入的路径（逗号分隔）
          <input
            aria-label="允许写入的路径"
            aria-required="true"
            aria-invalid={!!errors.allowedPaths}
            placeholder="例如：src/calculator.py, tests"
            value={draft.allowedPaths ?? 'src,tests'}
            onChange={(e) => field('allowedPaths', e.target.value)}
          />
          <small>请按方案填写本次可修改的文件或目录。</small>
          {errors.allowedPaths && (
            <span role="alert" className="field-error">
              {errors.allowedPaths}
            </span>
          )}
        </label>
        {mentioned.length > 0 && (
          <div className="field">
            <strong>方案提及的文件</strong>
            <div className="button-row">
              {mentioned.map((file) =>
                uncovered.includes(file) ? (
                  <Button
                    key={file}
                    onClick={() => field('allowedPaths', [...paths, file].join(', '))}
                  >
                    允许写入 {file}
                  </Button>
                ) : (
                  <span key={file} className="small-note">
                    {file} · 已在范围内
                  </span>
                ),
              )}
            </div>
            {paths.length > 0 && uncovered.length > 0 && (
              <label className="selection-item">
                <input
                  type="checkbox"
                  checked={limitedScope}
                  onChange={(e) => {
                    setLimitedScope(e.target.checked);
                    setErrors((current) => ({ ...current, allowedPaths: '' }));
                  }}
                />
                其余文件仅供读取
              </label>
            )}
          </div>
        )}
        <label className="field">
          补充限制（可选）
          <input
            value={draft.scope}
            onChange={(e) => field('scope', e.target.value)}
            placeholder="例如：保留现有函数签名"
          />
        </label>
        <div className="field-row">
          <label className="field">
            必须存在的文件（可选）
            <textarea
              aria-label="必须存在的文件"
              value={draft.requiredFiles || ''}
              onChange={(e) => field('requiredFiles', e.target.value)}
              aria-invalid={!!errors.requiredFiles}
              placeholder="每行一个相对路径，如 docs/guide.md"
            />
            <small>独立验收会逐项检查文件是否存在。</small>
            {errors.requiredFiles && (
              <span role="alert" className="field-error">
                {errors.requiredFiles}
              </span>
            )}
          </label>
          <label className="field">
            人工验收条件（可选）
            <textarea
              aria-label="人工验收条件"
              value={draft.criteria}
              onChange={(e) => field('criteria', e.target.value)}
              aria-invalid={!!errors.criteria}
              placeholder="每行一项，如：核对示例措辞"
            />
            <small>这些条件会在交付时列出，供你逐项核对。</small>
            {errors.criteria && (
              <span role="alert" className="field-error">
                {errors.criteria}
              </span>
            )}
          </label>
        </div>
        <button
          className="advanced-toggle"
          type="button"
          aria-expanded={advanced}
          onClick={() => setAdvanced(!advanced)}
        >
          <SlidersHorizontal size={16} />
          运行配置
          <span>
            {draft.model} · ${draft.budget.toFixed(2)} 上限
          </span>
        </button>
        {advanced && (
          <div className="advanced-fields">
            <div className="field-row">
              <label className="field">
                模型
                <select
                  aria-label="模型"
                  value={draft.model}
                  onChange={(e) => field('model', e.target.value)}
                >
                  <option value="configured">已配置的服务端模型</option>
                  <option value="fixture">确定性测试模型（仅 Runtime Lab）</option>
                </select>
              </label>
              <label className="field">
                预算上限（USD）
                <input
                  aria-label="预算上限（USD）"
                  type="number"
                  min=".1"
                  max="100"
                  step=".1"
                  value={draft.budget}
                  onChange={(e) => field('budget', Number(e.target.value))}
                />
                {errors.budget && <span className="field-error">{errors.budget}</span>}
              </label>
            </div>
            <div className="field">
              任务技能快照
              <div className="selection-list">
                {rt.state.skills
                  .filter((s) => s.enabled)
                  .map((s) => (
                    <label className="selection-item" key={s.id}>
                      <input
                        type="checkbox"
                        checked={draft.skills.includes(s.id)}
                        onChange={() =>
                          field(
                            'skills',
                            draft.skills.includes(s.id)
                              ? draft.skills.filter((x) => x !== s.id)
                              : [...draft.skills, s.id],
                          )
                        }
                      />
                      {s.name} <small>v{s.version}</small>
                    </label>
                  ))}
              </div>
              <small>创建时锁定技能与记忆；以后修改全局配置不改变此任务。</small>
            </div>
          </div>
        )}
        {advanced && (
          <div className="selection-list">
            <label className="selection-item">
              <input
                type="checkbox"
                checked={!!draft.allowExternal}
                onChange={(e) => field('allowExternal', e.target.checked)}
              />
              允许申请外部写入（每个操作另行审批）
            </label>
            <label className="selection-item">
              <input
                type="checkbox"
                checked={!!draft.allowDelegation}
                onChange={(e) => field('allowDelegation', e.target.checked)}
              />
              允许委派受限子任务
            </label>
            <label className="selection-item">
              <input
                type="checkbox"
                checked={!!draft.evidenceRecovery}
                onChange={(e) => field('evidenceRecovery', e.target.checked)}
              />
              启用证据驱动恢复（先诊断再修复，未知原因可暂停）
            </label>
            <div className="field">
              REA 参照目标（静态分析）
              {references.error && <p role="alert">{references.error}</p>}
              {!references.items.length && <small>管理员需先登记当前项目的参照目录快照。</small>}
              {references.items.map((target) => (
                <label className="selection-item" key={target.id}>
                  <input
                    type="checkbox"
                    checked={(draft.referenceTargets || []).includes(target.id)}
                    onChange={() =>
                      field(
                        'referenceTargets',
                        (draft.referenceTargets || []).includes(target.id)
                          ? (draft.referenceTargets || []).filter((id) => id !== target.id)
                          : [...(draft.referenceTargets || []), target.id],
                      )
                    }
                  />
                  {target.label} <small>{target.snapshot_digest.slice(0, 19)}</small>
                </label>
              ))}
              {references.cursor && (
                <Button onClick={() => void references.more()}>加载更多参照目标</Button>
              )}
            </div>
          </div>
        )}
        {advanced && (
          <div className="field">
            远程工具快照
            <label className="selection-item">
              <input
                type="checkbox"
                checked={!!draft.allowExternalRead}
                onChange={(e) => field('allowExternalRead', e.target.checked)}
              />
              允许审核过的远程只读工具及产物下载
            </label>
            {connections.error && <p role="alert">{connections.error}</p>}
            <div className="selection-list">
              {(connections.data || [])
                .filter((c: any) => c.status === 'active' && c.negotiated && c.tools.length)
                .map((c: any) => (
                  <label className="selection-item" key={c.id}>
                    <input
                      type="checkbox"
                      checked={(draft.connections || []).includes(c.id)}
                      onChange={() =>
                        field(
                          'connections',
                          (draft.connections || []).includes(c.id)
                            ? (draft.connections || []).filter((id) => id !== c.id)
                            : [...(draft.connections || []), c.id],
                        )
                      }
                    />
                    {c.name} · {c.protocol}
                  </label>
                ))}
            </div>
            <small>仅选中的连接进入本任务；远程写入仍需逐项审批。</small>
          </div>
        )}
        {errors.submit && (
          <div className="notice warning" role="alert">
            {errors.submit}
          </div>
        )}
        <div className="form-footer">
          <span className="muted">任务保存到服务端 · 草稿保存在浏览器</span>
          <div className="button-row">
            <Button onClick={close} disabled={busy}>
              保存草稿并关闭
            </Button>
            <Button primary type="submit" disabled={busy || stalePlan}>
              <Plus size={16} />
              {busy ? '正在创建…' : rt.createContext ? '确认并开始执行' : '创建任务'}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
function CommandSearch({ runtime: rt, close }: { runtime: Runtime; close: () => void }) {
  const [q, setQ] = useState('');
  const [all, setAll] = useState(false);
  const pages = Object.entries(pageLabels).filter(
    ([p, l]) => p !== 'task' && (l.includes(q) || p.includes(q.toLowerCase())),
  );
  const query = useCatalog<Execution>(
    '/runs?limit=8&q=' +
      encodeURIComponent(q) +
      (all || rt.route.project === '所有项目'
        ? ''
        : '&project=' + encodeURIComponent(rt.route.project)),
  );
  const runs = query.items;
  return (
    <Dialog title="快速前往" subtitle="搜索页面或任务 · Ctrl / ⌘ K" onClose={close}>
      <label className="search-field command-search">
        <Search size={18} />
        <input
          aria-label="全局搜索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="任务名称、编号或页面…"
          data-dialog-focus
        />
      </label>
      <label className="check-label">
        <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
        搜索所有项目
      </label>
      <div className="command-results">
        {pages.map(([page, label]) => (
          <button
            className="command-result"
            key={page}
            onClick={() => rt.app.navigate(page as Page)}
          >
            <span>{label}</span>
            <ChevronRight size={16} />
          </button>
        ))}
        {runs.map((r) => (
          <button
            className="command-result"
            key={r.id}
            onClick={() => rt.app.openRun(r.id, r.project)}
          >
            <span>
              {r.title}
              <small className="cell-sub">
                {r.id} · {r.project}
              </small>
            </span>
            <ArrowRight size={16} />
          </button>
        ))}
        {!pages.length && !runs.length && (
          <Empty title="没有匹配结果" description="调整关键词或搜索范围，不会影响现有任务。" />
        )}
      </div>
      {query.error && <p role="alert">{query.error}</p>}
      {query.cursor && <Button onClick={() => void query.more()}>更多匹配任务</Button>}
    </Dialog>
  );
}
