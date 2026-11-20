import { useState } from 'react';
import { BatteryLoader, CompensationAlert } from '../components/Notices.jsx';
import SagaTimeline from '../components/SagaTimeline.jsx';
import { usePoll } from '../lib/api.js';
import { FAILURE_REASON, SAGA_STATE, formatCop, formatDateTime } from '../lib/format.js';
import { TRANSACTIONS } from '../lib/queries.js';

const OUTCOME = {
  SUCCESS: ['Completada', 'status-CHARGING'],
  ROLLED_BACK: ['Revertida', 'status-RESERVED'],
  FAILED: ['Rechazada', 'status-UNKNOWN'],
  IN_PROGRESS: ['En curso', 'status-AVAILABLE'],
};

export default function Transactions() {
  const { data, error, loading } = usePoll(TRANSACTIONS, { limit: 30 });
  const [open, setOpen] = useState(null);
  const txs = data?.transactions ?? [];
  const rolledBack = txs.filter((t) => t.outcome === 'ROLLED_BACK').length;

  return (
    <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
      <h1 className="text-6xl">Transacciones</h1>
      <p className="mt-2 max-w-xl text-slate-600">
        Cada reserva es una transacción distribuida entre reservas, facturación y despacho de energía.
        {txs.length > 0 && ` De las últimas ${txs.length}, ${rolledBack} se revirtieron con compensaciones.`}
      </p>
      {error && <div className="mt-6"><CompensationAlert title="No se pudo leer el historial">{error.message}</CompensationAlert></div>}
      {loading && <div className="mt-10"><BatteryLoader label="Cargando historial" /></div>}
      {!loading && txs.length === 0 && (
        <p className="surface-quiet mt-8 p-6">Aún no hay transacciones. <a className="font-bold text-charge-deep underline" href="#/reservar">Reserva una carga</a> para ver aquí su recorrido.</p>
      )}
      <ul className="mt-8 grid gap-3">
        {txs.map((tx) => {
          const [label, cls] = OUTCOME[tx.outcome];
          const expanded = open === tx.id;
          return (
            <li key={tx.id} className="surface overflow-hidden">
              <button type="button" className="flex w-full flex-wrap items-center gap-x-6 gap-y-2 p-5 text-left"
                aria-expanded={expanded} onClick={() => setOpen(expanded ? null : tx.id)}>
                <span className={`chip ${cls}`}>{label}</span>
                <span className="font-bold">{tx.stationId} / {tx.connectorId}</span>
                <span className="text-slate-600">{tx.fleetId}, {tx.estimatedKwh} kWh</span>
                <span className="font-semibold tabular-nums">{formatCop(tx.amountCop)}</span>
                <span className="ml-auto text-sm text-slate-500">{formatDateTime(tx.createdAt)}</span>
              </button>
              {expanded && (
                <div className="grid gap-6 border-t border-paramo-deep p-5 md:grid-cols-2">
                  <div>
                    <h2 className="mb-3 text-2xl">Recorrido</h2>
                    <SagaTimeline tx={tx} />
                  </div>
                  <div className="grid content-start gap-3 text-sm">
                    <p><strong>Estado:</strong> {SAGA_STATE[tx.state]}</p>
                    {tx.failureReason && <p><strong>Motivo:</strong> {FAILURE_REASON[tx.failureReason] ?? tx.failureReason}</p>}
                    {tx.compensations.length > 0 && (
                      <div>
                        <strong>Compensaciones</strong>
                        <ul className="mt-1 list-disc pl-5">
                          {tx.compensations.map((c, i) => (
                            <li key={i}>{c.action === 'REFUND_FUNDS' ? 'Reembolso' : 'Liberar bahía'}: {c.status === 'DONE' ? 'hecho' : 'solicitado'}
                              {c.refundedCop != null && ` (${formatCop(c.refundedCop)})`}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {tx.stuck && <p className="font-bold text-coral">Reintentos agotados: requiere revisión del operador.</p>}
                    <p className="break-all text-slate-500">Correlation ID {tx.id}</p>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
