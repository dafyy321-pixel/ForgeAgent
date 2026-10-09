type Research = {
  full_cost_complete?: boolean;
  full_cost_usd?: number | null;
  missing_cost_components?: string[];
  failure_distribution?: Record<string, number>;
};
type Summary = {
  correct_disposition_rate: number;
  cluster_95_ci?: number[] | null;
  cost: number;
  latency_p95?: number | null;
  research?: Research;
};
type Comparison = {
  baseline: string;
  paired_difference: number;
  cluster_95_ci?: number[] | null;
  cost_ratio?: number | null;
  noninferiority_supported?: boolean;
};
type Report = {
  status: string;
  summaries?: Record<string, Summary>;
  comparisons?: Record<string, Comparison>;
  results?: { config: string; status: string; reserved?: number }[];
};
const percentage = (value: number) => `${(value * 100).toFixed(1)}%`;
const interval = (value?: number[] | null) =>
  value?.length === 2 ? value.map(percentage).join(' ～ ') : '样本不足，未计算';

export function ExperimentComparison({ report }: { report: Report }) {
  return (
    <>
      <div className="comparison-scroll">
        <table className="comparison-table" aria-label="实验配置比较">
          <thead>
            <tr>
              <th>配置</th>
              <th>实际修复</th>
              <th>预期处置符合率 / 95% 区间</th>
              <th>模型费用 / 预留</th>
              <th>完整费用</th>
              <th>P95 秒</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(report.summaries || {}).map(([name, summary]) => {
              const rows = (report.results || []).filter((row) => row.config === name);
              const missing = summary.research?.missing_cost_components || [];
              return (
                <tr key={name}>
                  <th scope="row">{name}</th>
                  <td>
                    {rows.filter((row) => row.status === 'SUCCEEDED').length} / {rows.length}
                  </td>
                  <td>
                    {percentage(summary.correct_disposition_rate)}
                    <br />
                    {interval(summary.cluster_95_ci)}
                  </td>
                  <td>
                    ${summary.cost.toFixed(4)} / $
                    {rows.reduce((sum, row) => sum + (row.reserved || 0), 0).toFixed(4)}
                  </td>
                  <td>
                    {summary.research?.full_cost_complete && summary.research.full_cost_usd != null
                      ? `$${summary.research.full_cost_usd.toFixed(4)}`
                      : `不完整${missing.length ? '：' + missing.join('、') : ''}`}
                  </td>
                  <td>{summary.latency_p95?.toFixed(2) ?? '尚无'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted">
        实际修复按 SUCCEEDED 计数；正确暂停可能满足预期处置，但不计入修复成功。运行中结果尚未定稿。
      </p>
      {Object.entries(report.comparisons || {}).map(([name, comparison]) => (
        <p key={name}>
          {name} 相对 {comparison.baseline}：处置符合率差 {percentage(comparison.paired_difference)}
          ； 配对 95% 区间 {interval(comparison.cluster_95_ci)}；模型费用比{' '}
          {comparison.cost_ratio?.toFixed(3) ?? '无法计算'}。
          {report.status !== 'completed'
            ? ' 等待完成与对账。'
            : comparison.noninferiority_supported
              ? ' 达到声明的非劣界限。'
              : ' 未建立非劣证据。'}
        </p>
      ))}
      {Object.entries(report.summaries || {}).map(([name, summary]) => (
        <p className="muted" key={name}>
          {name} 失败分布：
          {Object.entries(summary.research?.failure_distribution || {})
            .map(([category, n]) => `${category} ${n}`)
            .join('；') || '无已记录失败'}
          。
        </p>
      ))}
      <p className="muted">
        费用比仅比较模型账本。完整费用缺项或样本不足时，不能据此宣称总成本收益。
      </p>
    </>
  );
}
