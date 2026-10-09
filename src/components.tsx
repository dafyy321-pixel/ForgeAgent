import { useEffect, useRef, type ReactNode } from 'react';
import { X, Check, ArrowUpRight, Search, Circle, LoaderCircle } from './icons';
import type { RunStatus } from './types';
import { statusLabels } from './data';
export function Status({ status }: { status: RunStatus }) {
  return (
    <span className={`badge status-${status.toLowerCase()}`}>
      <i />
      {statusLabels[status]}
    </span>
  );
}
export function Button({
  children,
  onClick,
  primary = false,
  danger = false,
  disabled = false,
  className = '',
  type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  primary?: boolean;
  danger?: boolean;
  disabled?: boolean;
  className?: string;
  type?: 'button' | 'submit';
}) {
  return (
    <button
      type={type}
      className={`btn ${primary ? 'primary' : ''} ${danger ? 'danger' : ''} ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
export function Empty({
  title = '没有找到相关内容',
  description = '试试调整筛选条件，或创建一项新任务。',
  children,
}: {
  title?: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Search size={28} />
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function Heading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p className="muted">{description}</p>
      </div>
      <div className="heading-actions">{children}</div>
    </div>
  );
}
export function Panel({
  title,
  subtitle,
  action,
  children,
  className = '',
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-header">
          <div>
            <h3>{title}</h3>
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Progress({ value }: { value: number }) {
  return (
    <div className="progress-track" aria-label={`进度 ${value}%`}>
      <div className="progress-fill" style={{ width: `${value}%` }} />
    </div>
  );
}
export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? 'on' : ''}`}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
    >
      <span />
    </button>
  );
}
export function Dialog({
  title,
  subtitle,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const b = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const box = ref.current;
    const focusable = () =>
      Array.from(
        box?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),input,select,textarea,a[href],[tabindex="0"]',
        ) || [],
      );
    focusable()[0]?.focus();
    const listener = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeRef.current();
      }
      if (e.key === 'Tab') {
        const els = focusable();
        const first = els[0],
          last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener('keydown', listener);
    return () => {
      document.removeEventListener('keydown', listener);
      document.body.style.overflow = b;
      before?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`modal ${wide ? 'wide' : ''}`}
      >
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          <button className="icon-btn" aria-label="关闭弹窗" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
export function SparkChart({
  values,
  color = 'var(--accent)',
  height = 150,
  labels = true,
}: {
  values: number[];
  color?: string;
  height?: number;
  labels?: boolean;
}) {
  const max = Math.max(...values) * 1.15;
  const w = 620,
    h = height - 28;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * (h - 20)}`)
    .join(' ');
  return (
    <div className="spark-chart">
      <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label="最近七天任务趋势">
        <path
          d={`M 0 ${h} L ${points.replaceAll(' ', ' L ')} L ${w} ${h} Z`}
          fill={color}
          opacity=".07"
        />
        {[0, 1, 2, 3].map((i) => (
          <line
            key={i}
            x1="0"
            y1={20 + (i * (h - 20)) / 3}
            x2={w}
            y2={20 + (i * (h - 20)) / 3}
            stroke="var(--line)"
            strokeDasharray="3 5"
          />
        ))}
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth="2.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {values.map((v, i) => (
          <g key={i}>
            <circle
              cx={(i / (values.length - 1)) * w}
              cy={h - (v / max) * (h - 20)}
              r="4"
              fill="var(--surface)"
              stroke={color}
              strokeWidth="2"
            >
              <title>{`第 ${i + 1} 天：${v}`}</title>
            </circle>
            {labels && (
              <text
                x={(i / (values.length - 1)) * (w - 25) + 12}
                y={height - 3}
                textAnchor="middle"
                fontSize="10"
                fill="var(--muted)"
              >{`${i + 16} SEP`}</text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}
export function Metric({
  label,
  value,
  note,
  trend,
}: {
  label: string;
  value: ReactNode;
  note: string;
  trend?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {label}
        {trend && (
          <span className="positive">
            <ArrowUpRight size={13} />
            {trend}
          </span>
        )}
      </div>
      <strong>{value}</strong>
      <span className="muted">{note}</span>
    </div>
  );
}
export function StepIcon({ state }: { state: 'done' | 'active' | 'pending' }) {
  return (
    <span className={`step-icon ${state}`}>
      {state === 'done' ? (
        <Check size={14} />
      ) : state === 'active' ? (
        <LoaderCircle size={14} className="spin" />
      ) : (
        <Circle size={12} />
      )}
    </span>
  );
}
