import { STEP_LABEL, formatCop, formatTime } from '../lib/format.js';

const DOT = {
  SUCCEEDED: 'bg-pine',
  FAILED: 'bg-coral',
  TIMED_OUT: 'bg-coral',
  COMPENSATED: 'bg-amber',
  RETRYING: 'bg-amber',
};

const STATUS_TEXT = {
  SUCCEEDED: 'completado',
  FAILED: 'falló',
  TIMED_OUT: 'sin respuesta a tiempo',
  COMPENSATED: 'compensado',
  RETRYING: 'reintentando',
};

/** Línea de tiempo de una SAGA: los pasos de compensación se distinguen del camino feliz. */
export default function SagaTimeline({ tx }) {
  return (
    <ol className="relative ml-2 border-l-2 border-paramo-deep pl-6">
      {tx.steps.map((s, i) => {
        const compensation = s.status === 'COMPENSATED';
        const detail = s.detail ? JSON.parse(s.detail) : {};
        return (
          <li key={`${s.step}-${i}`} className="relative pb-4 last:pb-0">
            <span className={`absolute -left-[1.95rem] top-1 h-3.5 w-3.5 rounded-full ring-4 ring-white ${DOT[s.status] ?? 'bg-slate-400'}`} />
            <p className={`font-bold ${compensation ? 'text-amber-700' : ''}`}>
              {compensation && <span aria-hidden="true">↩ </span>}
              {STEP_LABEL[s.step] ?? s.step}
              <span className="ml-2 text-sm font-semibold text-slate-500">{STATUS_TEXT[s.status] ?? s.status}</span>
            </p>
            <p className="text-sm text-slate-500">
              {formatTime(s.at)}
              {detail.amount_cop != null && `, retenido ${formatCop(detail.amount_cop)}`}
              {detail.refunded_cop != null && `, reembolsado ${formatCop(detail.refunded_cop)}`}
              {detail.surge_applied && ', tarifa dinámica aplicada'}
              {detail.reason && `, ${detail.reason}`}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
