'use client';

/**
 * Bundles everything that's identical across EVERY case — model
 * version/validation note, benchmark performance stats, verified
 * architecture — into one collapsed-by-default section, clearly
 * separated from the patient-specific findings above it. This is
 * reference/audit material (useful for trust, not for reading this
 * particular case), so it shouldn't compete for attention with the
 * actual clinical content on every single page load.
 *
 * Native <details>/<summary> — no JS state needed, keyboard/screen-
 * reader accessible for free.
 */
export function ModelReferenceSection({ children }: { children: React.ReactNode }) {
  return (
    <details className="bg-bg2 border border-border rounded-rad2 group">
      <summary className="p-4 cursor-pointer list-none flex items-center justify-between text-xs uppercase tracking-wider text-text3 font-medium select-none">
        <span>Model reference &amp; validation status (same for every case)</span>
        <span className="text-text3 transition-transform group-open:rotate-180">▾</span>
      </summary>
      <div className="px-4 pb-4 flex flex-col gap-4 border-t border-border pt-4">{children}</div>
    </details>
  );
}
