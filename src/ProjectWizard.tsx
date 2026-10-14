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
    <Dialog title="接入 Git 仓库" subtitle="准备文件 → 上传预检 → 确认接入" onClose={onClose}>
      <p>
        接入后，可以讨论这个项目、确认修改方案，并下载代码补丁。请选择一个已提交的 Git
        版本；浏览器上传不会修改你的本地目录。
      </p>
      <details className="project-preparation">
        <summary>第一次接入？查看文件准备步骤</summary>
        <ol>
          <li>
            在准备接入的仓库目录中运行以下命令，复制完整版本号并生成上传文件：
            <pre>
              <code>{'git rev-parse HEAD\ngit bundle create reviewed.bundle HEAD'}</code>
            </pre>
          </li>
          <li>
            准备验收
            JSON，写明真正检查结果的命令（argv）和独立测试（protected_tests）。依赖镜像、锁文件和构建命令放在
            build 中。
            <p>
              可先运行 <code>forge project register 项目ID 仓库目录 acceptance.json --dry-run</code>{' '}
              检查文件和验收配置。
            </p>
          </li>
          <li>
            填写下方信息并选择文件。预检通过后，点击“登记已预检项目”。回到首页选择该项目即可开始讨论。
          </li>
        </ol>
        <p className="muted">
          当前接入 UTF-8 文本代码，展开后约 8 MiB；bundle 上限 16
          MiB。二进制、符号链接和子模块暂不支持。上传与登记需要管理员权限。
        </p>
      </details>
      <label className="field">
        项目 ID
        <input
          value={id}
          placeholder="例如 my-app-v1，版本更新时使用新 ID"
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
          placeholder="粘贴 git rev-parse HEAD 的完整输出"
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
