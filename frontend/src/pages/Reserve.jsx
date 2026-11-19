import { useEffect, useMemo, useState } from 'react';
import { BatteryLoader, CompensationAlert, SuccessToast } from '../components/Notices.jsx';
import SagaTimeline from '../components/SagaTimeline.jsx';
import Truck from '../components/Truck.jsx';
import { gql, usePoll } from '../lib/api.js';
import { FAILURE_REASON, SAGA_STATE, formatCop } from '../lib/format.js';
import { RESERVE, RESERVE_CONTEXT, TRANSACTION } from '../lib/queries.js';

const VEHICLES = {
  'flota-andina': ['VAN-ANDI-01', 'VAN-ANDI-02'],
  'flota-sabana-verde': ['VAN-SAB-01'],
  'flota-cafe-express': ['MOTO-CAFE-01'],
};
const REFERENCE_TARIFF = 1150; // COP/kWh, solo referencia visual; el monto real lo calcula billing-wallet

function useHashParams() {
  return useMemo(() => new URLSearchParams(window.location.hash.split('?')[1] ?? ''), []);
}

function LiveSaga({ id, onDone }) {
  const { data } = usePoll(TRANSACTION, { id }, { interval: 1200 });
  const tx = data?.transaction;
  useEffect(() => { if (tx?.isTerminal) onDone?.(tx); }, [tx?.isTerminal]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!tx) return <BatteryLoader label="Enviando la solicitud" />;
  return (
    <div className="grid gap-4">
      {!tx.isTerminal && <BatteryLoader label={SAGA_STATE[tx.state]} />}
      {tx.outcome === 'SUCCESS' && (
        <SuccessToast title="Carga activada">
          Bahía {tx.connectorId} de {tx.stationId} entregando energía. Cobro confirmado por {formatCop(tx.amountCop)}.
        </SuccessToast>
      )}
      {tx.outcome === 'ROLLED_BACK' && (
        <CompensationAlert title="La carga no se pudo activar y se revirtió">
          {FAILURE_REASON[tx.failureReason] ?? tx.failureReason} El saldo retenido volvió a la billetera y la bahía quedó libre.
        </CompensationAlert>
      )}
      {tx.outcome === 'FAILED' && (
        <CompensationAlert title="Reserva rechazada">{FAILURE_REASON[tx.failureReason] ?? tx.failureReason}</CompensationAlert>
      )}
      <SagaTimeline tx={tx} />
      <p className="text-xs text-slate-500">Correlation ID {tx.id}</p>
    </div>
  );
}

export default function Reserve() {
  const params = useHashParams();
  const { data, error } = usePoll(RESERVE_CONTEXT, {}, { interval: 5000 });
  const [form, setForm] = useState({
    stationId: params.get('station') ?? 'EST-BOG-080',
    connectorId: params.get('connector') ?? 'C1',
    fleetId: 'flota-andina',
    vehicleId: 'VAN-ANDI-01',
    estimatedKwh: 40,
  });
  const [sagaId, setSagaId] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [finished, setFinished] = useState(null);

  const station = data?.stations.find((s) => s.id === form.stationId);
  const connector = station?.connectors.find((c) => c.id === form.connectorId);
  const fleet = data?.fleets.find((f) => f.id === form.fleetId);
  const flags = Object.fromEntries((data?.featureFlags ?? []).map((f) => [f.name, f.enabled]));
  const ultraBlocked = connector?.tier === 'ULTRA' && !flags.ENABLE_FAST_CHARGE_BOOST;
  const reference = Math.ceil(form.estimatedKwh * REFERENCE_TARIFF * (connector?.tier === 'ULTRA' ? 1.15 : 1));
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  async function submit(e) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    setFinished(null);
    try {
      const { reserveAndCharge } = await gql(RESERVE, {
        input: { ...form, estimatedKwh: Number(form.estimatedKwh), fastCharge: connector?.tier === 'ULTRA',
          idempotencyKey: crypto.randomUUID() },
      });
      setSagaId(reserveAndCharge.transaction.id);
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto grid max-w-6xl gap-8 px-5 py-10 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section>
        <h1 className="text-6xl">Reservar y cargar</h1>
        <p className="mt-2 max-w-md text-slate-600">
          Se reserva la bahía, se retiene el saldo estimado y se ordena al cargador iniciar. Si algo falla, todo se revierte solo.
        </p>
        <div className="mt-8 flex h-24 items-end">
          <Truck parked={!sagaId || !!finished} width={200} />
        </div>
        {sagaId && (
          <div className="surface mt-6 p-6">
            <h2 className="mb-4 text-3xl">Seguimiento</h2>
            <LiveSaga key={sagaId} id={sagaId} onDone={setFinished} />
          </div>
        )}
      </section>

      <form className="av-checkout" onSubmit={submit} aria-label="Resumen de reserva">
        <div className="av-checkout__card av-checkout__cart">
          <p className="av-checkout__title">Resumen de reserva</p>
          <div className="av-checkout__steps">
            {error && <CompensationAlert title="No se pudieron cargar los datos">{error.message}</CompensationAlert>}
            <div>
              <label className="av-checkout__section-label" htmlFor="station">Electrolinera</label>
              <select id="station" className="av-field" value={form.stationId}
                onChange={(e) => set({ stationId: e.target.value, connectorId: 'C1' })}>
                {data?.stations.map((s) => <option key={s.id} value={s.id}>{s.name}, {s.city}</option>)}
              </select>
            </div>
            <div>
              <label className="av-checkout__section-label" htmlFor="connector">Conector</label>
              <select id="connector" className="av-field" value={form.connectorId} onChange={(e) => set({ connectorId: e.target.value })}>
                {station?.connectors.map((c) => (
                  <option key={c.id} value={c.id} disabled={c.status !== 'AVAILABLE'}>
                    {c.id}: {c.type} {c.powerKw} kW{c.tier === 'ULTRA' ? ' ultra' : ''}{c.status !== 'AVAILABLE' ? ' (ocupado)' : ''}
                  </option>
                ))}
              </select>
              {ultraBlocked && <p className="mt-1 text-sm text-coral">La ultra-carga está deshabilitada en este ambiente.</p>}
            </div>
            <hr />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="av-checkout__section-label" htmlFor="fleet">Flota</label>
                <select id="fleet" className="av-field" value={form.fleetId}
                  onChange={(e) => set({ fleetId: e.target.value, vehicleId: VEHICLES[e.target.value]?.[0] })}>
                  {data?.fleets.map((f) => <option key={f.id} value={f.id}>{f.name ?? f.id}</option>)}
                </select>
              </div>
              <div>
                <label className="av-checkout__section-label" htmlFor="vehicle">Vehículo</label>
                <select id="vehicle" className="av-field" value={form.vehicleId} onChange={(e) => set({ vehicleId: e.target.value })}>
                  {(VEHICLES[form.fleetId] ?? []).map((v) => <option key={v}>{v}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="av-checkout__section-label" htmlFor="kwh">Energía estimada: {form.estimatedKwh} kWh</label>
              <input id="kwh" type="range" min="5" max="200" step="5" value={form.estimatedKwh}
                onChange={(e) => set({ estimatedKwh: Number(e.target.value) })} className="w-full accent-[#ee8f3a]" />
            </div>
            <hr />
            <div>
              <span className="av-checkout__section-label">Saldo</span>
              <dl className="av-checkout__details">
                <dt>Disponible en la billetera</dt><dd>{formatCop(fleet?.availableCop)}</dd>
                <dt>Retenido por otras cargas</dt><dd>{formatCop(fleet?.heldCop)}</dd>
                <dt>Tarifa de referencia</dt><dd>{formatCop(REFERENCE_TARIFF)} / kWh</dd>
                {flags.ENABLE_DYNAMIC_SURGE_PRICING && <><dt>Tarifa dinámica</dt><dd>activa de 5 a 9 p. m.</dd></>}
              </dl>
            </div>
            {submitError && <CompensationAlert title="La solicitud no se envió">{submitError}</CompensationAlert>}
          </div>
        </div>
        <div className="av-checkout__card av-checkout__footer">
          <div>
            <p className="text-sm text-slate-600">Retención estimada</p>
            <p className="av-checkout__price">{formatCop(reference)}</p>
          </div>
          <button type="submit" className="btn-charge" disabled={submitting || !connector || connector.status !== 'AVAILABLE' || ultraBlocked}>
            {submitting ? 'Enviando…' : 'Reservar y cargar'}
          </button>
        </div>
      </form>
    </main>
  );
}
