const cop = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
const time = new Intl.DateTimeFormat('es-CO', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'America/Bogota' });
const dateTime = new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Bogota' });

export const formatCop = (n) => (n == null ? '—' : cop.format(n));
export const formatTime = (iso) => (iso ? time.format(new Date(iso)) : '—');
export const formatDateTime = (iso) => (iso ? dateTime.format(new Date(iso)) : '—');

export const CONNECTOR_STATUS = {
  AVAILABLE: 'Libre',
  RESERVED: 'Reservado',
  CHARGING: 'Cargando',
  OUT_OF_SERVICE: 'Fuera de servicio',
  UNKNOWN: 'Sin señal',
};

export const SAGA_STATE = {
  RESERVING: 'Reservando bahía',
  AUTHORIZING_FUNDS: 'Reteniendo saldo',
  STARTING_CHARGE: 'Activando cargador',
  CAPTURING: 'Confirmando cobro',
  COMPLETED: 'Carga activa',
  COMPENSATING_FUNDS: 'Reembolsando saldo',
  COMPENSATING_RESERVATION: 'Liberando bahía',
  COMPENSATED: 'Revertida',
  FAILED: 'Rechazada',
};

export const STEP_LABEL = {
  SAGA_STARTED: 'Solicitud recibida',
  RESERVE_CONNECTOR: 'Reserva de bahía',
  AUTHORIZE_FUNDS: 'Retención de saldo',
  START_CHARGING: 'Activación del cargador',
  CAPTURE_FUNDS: 'Cobro confirmado',
  REFUND_FUNDS: 'Reembolso a la billetera',
  RELEASE_CONNECTOR: 'Bahía liberada',
};

export const FAILURE_REASON = {
  HARDWARE_FAULT_SIMULATED: 'El cargador reportó una falla física (falla simulada activa en este ambiente).',
  INSUFFICIENT_FUNDS: 'La billetera de la flota no tiene saldo suficiente para la retención estimada.',
  CONNECTOR_NOT_AVAILABLE: 'Otra flota tomó esta bahía. Elige otro conector.',
  FAST_CHARGE_DISABLED: 'La ultra-carga no está habilitada en este ambiente.',
  CHARGER_ERROR: 'El cargador no respondió a la orden de inicio.',
  CHARGER_UNREACHABLE_CIRCUIT_OPEN: 'El sistema de cargadores está caído; se canceló sin esperar (circuito abierto).',
  CHARGER_TIMEOUT: 'El cargador no confirmó a tiempo; se canceló la sesión.',
  CONNECTOR_NOT_FOUND: 'El conector no existe en esta estación.',
};

export const ROLE_LABEL = {
  'fleet-operator': 'Operador de flota',
  'platform-admin': 'Administrador de plataforma',
  auditor: 'Auditor',
};
