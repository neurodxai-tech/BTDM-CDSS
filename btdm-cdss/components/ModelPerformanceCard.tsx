'use client';

/**
 * Numbers below are taken from the npj Digital Medicine manuscript
 * (Part 1, final version): Table 3 (held-out test set, Seed 42), Table 5
 * (ten-fold CV) and the Methods (ANIAS protocol, IRC No. 520260158).
 *
 * They describe the SEED 42 checkpoint. The card asks the backend which
 * checkpoint is actually loaded (/health -> checkpoint_id) and shows a
 * warning if it is not Seed 42, so the UI can never silently present
 * Seed 42 numbers next to a different model's predictions.
 */
import { useEffect, useState } from 'react';
import { getHealth } from '@/lib/api';

export function ModelPerformanceCard() {
  const [loaded, setLoaded] = useState<string | null | undefined>(undefined);
  const [matches, setMatches] = useState(true);

  useEffect(() => {
    getHealth().then((h) => {
      if (!h) return;
      setLoaded(h.checkpoint_id);
      setMatches(h.checkpoint_matches_manuscript);
    });
  }, []);

  return (
    <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-5">
      {!matches && (
        <div className="bg-glioma/10 border border-glioma/30 rounded-rad p-3 text-xs text-glioma leading-relaxed">
          <span className="font-medium">Checkpoint mismatch:</span> the backend has loaded{' '}
          <span className="font-mono">{loaded ?? 'no checkpoint'}</span>, but the numbers below
          describe the Seed 42 checkpoint reported in the manuscript. Predictions from this server
          are not covered by these metrics.
        </div>
      )}
      <Section title="Test-set performance">
        <p className="text-[0.68rem] text-text3 -mt-1 mb-1">
          Seed 42 checkpoint, held-out benchmark test set (N = 1,600) — same sources as training,
          not external data.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <Stat value="95.50%" label="Accuracy" />
          <Stat value="95.40%" label="Macro F1" />
          <Stat value="99.49%" label="AUC-ROC" />
          <Stat value="0.8300" label="Glioma recall (lowest)" />
        </div>
        <div className="mt-2 pt-2 border-t border-border text-xs text-text2">
          <span className="font-mono text-text">99.02 ± 0.61%</span> · 10-fold CV · Macro F1
          <br />
          95% CI [98.58, 99.45] · no significant difference from cross-entropy-only training
        </div>
      </Section>

      <Section title="Training config">
        <KV k="Optimiser" v="AdamW · LR 1e-4 · WD 1e-5" />
        <KV k="Scheduler" v="CosineAnnealing T₀=10, T_mult=2" />
        <KV k="Batch / epochs" v="32 · max 50 · patience 10" />
        <KV k="Seeds" v="42, 123, 456, 789, 101112" />
      </Section>

      <Section title="Pre-specified criteria (ANIAS, Part 2)">
        <KV k="Primary" v="Macro F1 ≥ 0.85" />
        <KV k="SC1" v="Glioma recall ≥ 0.80 (malignant disease safety floor)" />
        <KV k="SC2" v="No-tumour recall ≥ 0.95 (limits healthy-patient false alarms)" />
        <div className="text-xs text-text2 mt-1">
          Confidence gate: max softmax ≥ 0.70 — below this, every result is flagged for
          mandatory specialist review regardless of predicted class.
        </div>
      </Section>

      <Section title="Training data">
        <div className="text-xs text-text2 leading-relaxed">
          Figshare (glioma / meningioma / pituitary) · SARTAJ (glioma / meningioma / pituitary) ·
          Br35H (no-tumour only)
        </div>
        <div className="text-[0.68rem] text-text3 mt-1">
          3 public datasets · MD5-hash source assignment. Every no-tumour image comes from Br35H,
          so source and class are confounded: part of the no-tumour performance may reflect
          recognising Br35H acquisition characteristics rather than anatomy.
        </div>
      </Section>

      <Section title="Classes">
        <div className="text-xs text-text2 leading-relaxed">
          Glioma · Meningioma · Pituitary / PitNET · No tumour detected
        </div>
        <div className="text-[0.68rem] text-text3 mt-1">
          Training data spans WHO Gr 1–4 (glioma) and Gr 1–3 (meningioma) cases, but this model
          classifies tumour <span className="text-text2">type</span> only — it does NOT determine
          WHO grade for any individual case. Grading is a histopathological/molecular assessment
          (mitotic activity, necrosis, IDH/1p19q/MGMT status per WHO CNS5) that imaging
          classification alone cannot provide.
        </div>
      </Section>

      <div className="bg-warn/10 border border-warn/25 rounded-rad p-3 text-xs text-warn leading-relaxed">
        ⏳ <span className="font-medium">ANIAS external validation:</span> cohort study pending
        (target N=600, 150/class; minimum 100/class). IRC Protocol No. 520260158. The numbers above
        are in-distribution benchmark results; external validity has not been established. Research
        use only — not for patient management.
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">{title}</div>
      {children}
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="font-mono text-lg text-text">{value}</div>
      <div className="text-[0.68rem] text-text3">{label}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2 text-xs">
      <span className="text-text3 flex-shrink-0 w-28">{k}</span>
      <span className="text-text2">{v}</span>
    </div>
  );
}
