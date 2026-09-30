'use client';

const ROWS: { chip: string; desc: string }[] = [
  { chip: 'CNN', desc: 'EfficientNet-B0 · single-channel input (RGB weights averaged) · GAP → 1280-d' },
  { chip: 'TRANS', desc: '6-layer Transformer · 8 heads · MLP 2048 · patch=16 · GAP → 512-d' },
  { chip: 'JOINT', desc: 'Concat CNN ‖ Transformer → 1792-d' },
  { chip: 'HEAD', desc: 'Linear(1792,256) → ReLU → Dropout(0.5) → Linear(256,4)' },
  { chip: 'GRL', desc: 'Domain discriminator (Figshare/SARTAJ/Br35H), λ₁=0.30, inside domain_clf' },
  { chip: 'SCL', desc: 'SupCon projection: Linear(1792,512)→ReLU→Dropout(0.10)→Linear(512,128), L2-norm, τ=0.10' },
];

/**
 * Static architecture reference. Every line here was verified against
 * the actual training notebook (BTDM_FINAL.ipynb), not just the
 * manuscript's prose — the two disagreed on the head structure, and this
 * card reflects the code, which won. See the backend's
 * weights/README.md for the full correction log.
 */
export function ArchitectureCard() {
  return (
    <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-text3 font-medium">Verified architecture</div>
        <span className="text-[0.68rem] font-mono text-text3">25,057,731 params</span>
      </div>
      <div className="flex flex-col gap-2">
        {ROWS.map((r) => (
          <div key={r.chip} className="flex items-start gap-2.5 text-[0.8rem]">
            <span className="bg-accent/12 text-accent2 rounded px-1.5 py-0.5 font-mono text-[0.68rem] flex-shrink-0 mt-0.5">
              {r.chip}
            </span>
            <span className="text-text2">{r.desc}</span>
          </div>
        ))}
      </div>
      <p className="text-[0.68rem] text-text3 pt-2 border-t border-border leading-relaxed">
        Parameter count matches the manuscript exactly (EfficientNet-B0 4,006,972 ·
        Transformer 19,147,264 · heads 1,903,495).
      </p>
    </div>
  );
}
