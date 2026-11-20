// Avisos adaptados de Uiverse.io: éxito por akshat-patel28 (quick-baboon-29), alerta por
// seyed-mohsen-mousavi (heavy-cobra-18) y batería por doniaskima (itchy-baboon-21). MIT.

export function BatteryLoader({ label }) {
  return (
    <div className="flex items-center gap-4" role="status" aria-live="polite">
      <div className="av-battery" aria-hidden="true" />
      <span className="font-semibold">{label}</span>
    </div>
  );
}

export function SuccessToast({ title, children, onClose }) {
  return (
    <div className="av-toast" role="status">
      <svg className="av-toast__wave" viewBox="0 0 1440 320" aria-hidden="true">
        <path d="M0,256L69,192C137,235,206,213,274,149C343,181,411,283,480,251C549,181,617,208,686,256C754,123,823,165,891,224C960,112,1029,197,1097,144C1166,133,1234,219,1303,133C1371,192,1440,64,1440,64L1440,320L0,320Z" />
      </svg>
      <div className="av-toast__icon" aria-hidden="true">
        <svg viewBox="0 0 512 512" width="17" height="17" fill="currentColor">
          <path d="M256 48a208 208 0 1 1 0 416 208 208 0 1 1 0-416zm0 464A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM369 209c9.4-9.4 9.4-24.6 0-33.9s-24.6-9.4-33.9 0l-111 111-47-47c-9.4-9.4-24.6-9.4-33.9 0s-9.4 24.6 0 33.9l64 64c9.4 9.4 24.6 9.4 33.9 0L369 209z" />
        </svg>
      </div>
      <div className="flex-1">
        <p className="av-toast__title">{title}</p>
        <div className="text-sm text-slate-600">{children}</div>
      </div>
      {onClose && (
        <button type="button" className="btn-ghost !p-1" onClick={onClose} aria-label="Cerrar aviso">✕</button>
      )}
    </div>
  );
}

export function CompensationAlert({ title, children, onClose }) {
  return (
    <div className="av-alert" role="alert">
      <div className="flex gap-3">
        <div className="av-alert__icon" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor" width="24" height="24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
          </svg>
        </div>
        <div>
          <p className="font-bold">{title}</p>
          <div className="text-sm text-slate-300">{children}</div>
        </div>
      </div>
      {onClose && (
        <button type="button" className="av-alert__close" onClick={onClose} aria-label="Cerrar alerta">✕</button>
      )}
    </div>
  );
}
