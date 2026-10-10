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
  repair_rate?: number;
  repair_cluster_95_ci?: number[] | null;
  correct_pauses?: number;
  manual_decisions?: number;
  success_attempt_cost_usd?: number;
};
type Comparison = {
  baseline: string;
  paired_difference: number;
  cluster_95_ci?: number[] | null;
  cost_ratio?: number | null;
  noninferiority_supported?: boolean;
  paired_repair_difference?: number;
  negative_transfer_cases?: { case_id: string; repair_difference: number }[];
  per_project_repair_difference?: Record<string, number>;
};
type Report = {
  status: string;
  summaries?: Record<string, Summary>;
  comparisons?: Record<string, Comparison>;
  results?: { config: string; status: string; verdict?: string | null; reserved?: number }[];
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
                    {summary.repair_rate != null && (
                      <>
                        <br />
                        PASS 修复率 {percentage(summary.repair_rate)}
                      </>
                    )}
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
        完成数量按 SUCCEEDED 计数；PASS
        修复率还要求独立验收通过。正确暂停不计入修复成功。运行中结果尚未定稿。
      </p>
      {Object.entries(report.comparisons || {}).map(([name, comparison]) => (
        <div key={name}>
          <p>
            {name} 相对 {comparison.baseline}：处置符合率差{' '}
            {percentage(comparison.paired_difference)}； 配对 95% 区间{' '}
            {interval(comparison.cluster_95_ci)}；模型费用比{' '}
            {comparison.cost_ratio?.toFixed(3) ?? '无法计算'}。
            {report.status !== 'completed'
              ? ' 等待完成与对账。'
              : comparison.noninferiority_supported
                ? ' 达到声明的非劣界限。'
                : ' 未建立非劣证据。'}
          </p>
          {comparison.paired_repair_difference != null && (
            <p>
              实际修复率差 {percentage(comparison.paired_repair_difference)}；负迁移任务{' '}
              {comparison.negative_transfer_cases?.length ?? 0} 个。
              {(comparison.negative_transfer_cases || [])
                .map((item) => ` ${item.case_id} ${percentage(item.repair_difference)}`)
                .join('；')}
              {Object.entries(comparison.per_project_repair_difference || {})
                .map(([project, value]) => ` ${project}：${percentage(value)}`)
                .join('；')}
            </p>
          )}
        </div>
      ))}
      {Object.entries(report.summaries || {}).map(([name, summary]) => (
        <p className="muted" key={name}>
          {name} 失败分布：
          {Object.entries(summary.research?.failure_distribution || {})
            .map(([category, n]) => `${category} ${n}`)
            .join('；') || '无已记录失败'}
          。
          {summary.correct_pauses != null &&
            ` 正确暂停 ${summary.correct_pauses} 次，人工决策 ${summary.manual_decisions ?? 0} 次。`}
          {summary.success_attempt_cost_usd != null &&
            ` 成功尝试模型费用 $${summary.success_attempt_cost_usd.toFixed(4)}；上表模型费用包含失败尝试。`}
        </p>
      ))}
      <p className="muted">
        费用比仅比较模型账本。完整费用缺项或样本不足时，不能据此宣称总成本收益。
      </p>
    </>
  );
}
