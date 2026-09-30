'use client';

import type { AnalysisResult, TumourClass } from '@/lib/types';

const CLASS_META: Record<TumourClass, { label: string; color: string }> = {
  glioma: { label: 'Glioma', color: '#e85d5d' },
  meningioma: { label: 'Meningioma', color: '#a855f7' },
  pituitary: { label: 'Pituitary', color: '#22d3b0' },
  notumor: { label: 'No tumour', color: '#4ade80' },
};

/**
 * The headline finding — predicted class, confidence, entropy, and the
 * low-confidence gate warning. Deliberately full-width and the FIRST
 * thing on the page: this is the one fact a clinician needs before
 * anything else, so it shouldn't be siloed in a side column at the same
 * level as secondary detail. Everything else on the page supports or
 * qualifies this one line.
 */
export function PrimaryFindingBanner({ result }: { result: AnalysisResult }) {
  const predicted = CLASS_META[result.predictedClass];
  return (
    <div className="flex flex-col gap-3">
      <div className="bg-bg2 border border-border rounded-rad2 p-5 flex items-center gap-5">
        <div
          className="rounded-rad2 flex items-center justify-center text-2xl flex-shrink-0"
          style={{ background: `${predicted.color}22`, color: predicted.color, width: 52, height: 52 }}
        >
          ●
        </div>
        <div>
          <div className="font-serif text-2xl tracking-tight" style={{ color: predicted.color }}>
            {predicted.label}
          </div>
          <div className="font-mono text-sm text-text2 mt-0.5">
            Calibrated confidence {(result.calibratedConfidence * 100).toFixed(1)}%
          </div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-xs text-text3">Predictive entropy</div>
          <div className="font-mono text-sm text-text">{result.predictiveEntropy.toFixed(2)}</div>
        </div>
      </div>

      {result.belowConfidenceGate && (
        <div className="bg-glioma/10 border border-glioma/30 rounded-rad p-4 text-sm text-[#fca5a5] flex gap-3">
          <span className="font-semibold">LOW CONFIDENCE (&lt; 70% gate)</span> — this study is flagged for
          mandatory specialist review. The model prediction alone is not sufficient for a clinical decision.
        </div>
      )}
    </div>
  );
}

/** Secondary detail — the full probability breakdown behind the headline
 * finding above. Useful for understanding HOW confident the call was
 * relative to alternatives, but not the first thing to look at. */
export function ClassificationPanel({ result }: { result: AnalysisResult }) {
  return (
    <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-3.5">
      <div className="text-xs uppercase tracking-wider text-text3 font-medium">Class probabilities</div>
      {(Object.keys(CLASS_META) as TumourClass[]).map((cls) => {
        const meta = CLASS_META[cls];
        const pct = result.probabilities[cls] * 100;
        return (
          <div key={cls} className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full" style={{ background: meta.color }} />
                {meta.label}
              </span>
              <span className="font-mono text-text2">{pct.toFixed(1)}%</span>
            </div>
            <div className="h-2 bg-bg4 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${pct}%`, background: meta.color }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ModelInfoFooter({ result }: { result: AnalysisResult }) {
  return (
    <div className="bg-bg3 border border-border rounded-rad p-4 text-xs text-text2 leading-relaxed">
      <div className="text-text font-medium mb-1">{result.model.name} · {result.model.version}</div>
      {result.model.validationNote}
    </div>
  );
}
