import { useState } from 'react';
import { api } from './api';
import { Button, Dialog } from './components';

export function ProjectWizard({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [id, setId] = useState('');
  const [commit, setCommit] = useState('');
  const [bundle, setBundle] = useState<File | null>(null);
  const [contract, setContract] = useState<File | null>(null);
  const [payload, setPayload] = useState<unknown>();
  const [result, setResult] = useState<{
    files: number;
    commit: string;
    baseline_digest: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const invalidate = () => {
    setPayload(undefined);
    setResult(null);
  };
  async function preflight() {
    setBusy(true);
    setError('');
    invalidate();
    try {
      if (!id.trim() || !/^[a-f0-9]{40}([a-f0-9]{24})?$/.test(commit) || !bundle || !contract)
        throw Error('填写项目 ID、完整 commit，并选择 Git bundle 和验收 JSON。');
      if (bundle.size > 16 * 1024 * 1024 || contract.size > 2 * 1024 * 1024)
        throw Error('bundle 上限 16 MiB；验收与元数据 JSON 上限 2 MiB。');
      const acceptance = JSON.parse(await contract.text());
      const { build = {}, ...tests } = acceptance;
      const checksum = Array.from(
        new Uint8Array(await crypto.subtle.digest('SHA-256', await bundle.arrayBuffer())),
        (b) => b.toString(16).padStart(2, '0'),
      ).join('');
      const next = {
        id: id.trim(),
        name: id.trim(),
        repository: { commit, bundle_digest: 'sha256:' + checksum, bundle_bytes: bundle.size },
        acceptance_id: tests.id,
        verification_argv: tests.argv,
        acceptance: tests,
        build,
      };
      if (new TextEncoder().encode(JSON.stringify(next)).length > 2 * 1024 * 1024)
        throw Error('验收与元数据 JSON 超过 2 MiB。');
      await api('/repository-bundles/' + checksum, 'PUT', undefined, undefined, {
        raw: bundle,
        timeoutMs: 120000,
      });
      const report = await api<{ files: number; commit: string; baseline_digest: string }>(
        '/projects/preflight',
        'POST',
        next,
        undefined,
        { timeoutMs: 120000 },
      );
      setPayload(next);
      setResult(report);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title="接入 Git 仓库"
      subtitle="固定版本 → 大小与路径检查 → 登记审核后的验收"
      onClose={onClose}
    >
      <p>
        先使用 CLI 的 <code>forge project register --dry-run</code> 检查本地仓库；本向导接收该固定
        commit 的 Git bundle。依赖镜像、锁文件及验收命令写在受审验收 JSON 中。
      </p>
      <label className="field">
        项目 ID
        <input
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            invalidate();
          }}
        />
      </label>
      <label className="field">
        完整 Git commit
        <input
          value={commit}
          onChange={(e) => {
            setCommit(e.target.value.trim());
            invalidate();
          }}
        />
      </label>
      <label className="field">
        Git bundle
        <input
          type="file"
          accept=".bundle"
          onChange={(e) => {
            setBundle(e.target.files?.[0] || null);
            invalidate();
          }}
        />
      </label>
      <label className="field">
        验收与依赖 JSON
        <input
          type="file"
          accept=".json"
          onChange={(e) => {
            setContract(e.target.files?.[0] || null);
            invalidate();
          }}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      {result && (
        <div role="status">
          <p>
            预检通过：{result.files} 个文件，固定 commit {result.commit}。
          </p>
          <p>尚未执行独立验收。登记后可创建受预算约束的任务；配置的沙箱与依赖环境仍需实际检查。</p>
          <details>
            <summary>基线完整性摘要</summary>
            <code>{result.baseline_digest}</code>
          </details>
        </div>
      )}
      <div className="form-footer">
        <Button disabled={busy} onClick={() => void preflight()}>
          上传并预检
        </Button>
        <Button
          primary
          disabled={busy || !payload}
          onClick={async () => {
            setBusy(true);
            try {
              await api('/projects', 'POST', payload, undefined, { timeoutMs: 120000 });
              onDone();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          登记已预检项目
        </Button>
      </div>
    </Dialog>
  );
}
