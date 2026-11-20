import Truck from './Truck.jsx';

/**
 * Hero: la carretera de la Sabana al amanecer, con una electrolinera al borde de la vía.
 * Ilustración SVG propia inspirada en la referencia de carretera; el camión es el de Uiverse.
 */
export default function RoadHero({ title, children, driving = true, compact = false }) {
  return (
    <section className="road-hero" style={{ minHeight: compact ? 300 : 470 }}>
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1200 420" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <defs>
          <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#bfe3ee" />
            <stop offset="0.55" stopColor="#f6dcc0" />
            <stop offset="1" stopColor="#fbe9d2" />
          </linearGradient>
          <linearGradient id="road" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#5b6f86" />
            <stop offset="1" stopColor="#2a3d57" />
          </linearGradient>
        </defs>
        <rect width="1200" height="420" fill="url(#sky)" />
        <circle cx="830" cy="200" r="46" fill="#fff4dc" opacity="0.9" />
        {/* cerros de la Sabana en capas */}
        <path d="M0 250 C140 190 260 210 380 232 C520 255 610 180 760 205 C900 228 1010 190 1200 220 L1200 420 L0 420Z" fill="#c9d9c9" />
        <path d="M0 285 C160 238 300 262 450 270 C600 278 700 240 860 252 C1010 263 1100 240 1200 255 L1200 420 L0 420Z" fill="#9fcf9a" />
        <path d="M0 320 C220 290 380 300 560 302 L640 302 C820 300 980 288 1200 312 L1200 420 L0 420Z" fill="#7cbf73" />
        {/* carretera en perspectiva */}
        <path d="M585 302 L615 302 L860 420 L340 420Z" fill="url(#road)" />
        <path d="M598 302 L602 302 L612 420 L588 420Z" fill="#fcd116" opacity="0.95" />
        <path d="M594 302 L585 302 L340 420 L360 420Z" fill="#e8eef0" />
        <path d="M606 302 L615 302 L860 420 L840 420Z" fill="#e8eef0" />
        {/* pinos */}
        {[[455, 300, 0.7], [505, 308, 0.55], [740, 292, 0.65], [1010, 296, 1.1], [1080, 306, 0.85], [1150, 302, 0.7]].map(([x, y, s]) => (
          <g key={`${x}`} transform={`translate(${x} ${y}) scale(${s})`}>
            <rect x="-4" y="-6" width="8" height="26" fill="#6b4a2f" />
            <path d="M0 -90 L28 -30 L14 -30 L36 6 L-36 6 L-14 -30 L-28 -30Z" fill="#2f6b4f" />
          </g>
        ))}
        {/* electrolinera al borde de la vía */}
        <g transform="translate(900 268)">
          <rect x="0" y="0" width="120" height="10" rx="3" fill="#13233a" />
          <rect x="10" y="10" width="6" height="58" fill="#13233a" />
          <rect x="104" y="10" width="6" height="58" fill="#13233a" />
          <rect x="44" y="28" width="30" height="42" rx="6" fill="#ee8f3a" />
          <path d="M61 34 L52 50 L59 50 L56 63 L67 45 L60 45Z" fill="#fff" />
        </g>
      </svg>
      <div className="relative z-10 mx-auto flex max-w-6xl flex-col gap-3 px-5 pt-10 pb-44 sm:px-8">
        <h1 className="max-w-xl text-5xl text-andes sm:text-7xl">{title}</h1>
        {children}
      </div>
      <div className={`road-hero__truck ${driving ? 'road-hero__truck--drive' : ''}`} style={{ '--truck-scale': compact ? 0.6 : 0.85 }}>
        <Truck parked={!driving} />
      </div>
    </section>
  );
}
