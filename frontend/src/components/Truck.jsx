// Camión de reparto eléctrico — markup de Uiverse.io por vikas7754 (wet-goose-61), MIT.
const spokes = Array.from({ length: 7 }, (_, i) => i);

function Wheel({ position }) {
  return (
    <div className={`truck__wheel truck__wheel--${position}`}>
      <div className="truck__wheel-arch" />
      <div className="truck__wheel-arch-trim truck__wheel-arch-trim--top" />
      <div className="truck__wheel-arch-trim truck__wheel-arch-trim--left" />
      <div className="truck__wheel-arch-trim truck__wheel-arch-trim--right" />
      <div className="truck-wheel">
        <div className="truck-wheel__rim">
          {spokes.map((i) => <div key={i} style={{ '--index': i }} className="truck-wheel__spoke" />)}
        </div>
      </div>
    </div>
  );
}

export default function Truck({ parked = false, width = 220 }) {
  return (
    <div className={`truck ${parked ? 'truck--parked' : ''}`} style={{ '--width': width }} aria-hidden="true">
      <div className="truck__body">
        <div className="truck__body truck__body--top">
          <div className="truck__window"><div className="truck__window-glass" /></div>
        </div>
        <div className="truck__body truck__body--mid"><div className="truck__mid-body" /></div>
        <div className="truck__body truck__body--bottom">
          <div className="truck__underpanel" />
          <div className="truck__rear-bumper" />
          <div className="truck__side-skirt" />
        </div>
      </div>
      <Wheel position="front" />
      <Wheel position="rear" />
      <div className="truck__headlight" />
      <div className="truck__taillight" />
      <div className="truck__indicator" />
      <div className="truck__foglight" />
    </div>
  );
}
