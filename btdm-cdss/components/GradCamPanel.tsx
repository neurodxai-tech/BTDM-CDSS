'use client';

import { useEffect, useState } from 'react';
import { fetchGradcamObjectUrl, type GradcamMode } from '@/lib/api';
import type { LesionMeasurement, WeakLocalization } from '@/lib/types';

const MODE_OPTIONS: { mode: GradcamMode; label: string }[] = [
  { mode: 'region', label: 'Region' },
  { mode: 'heatmap', label: 'Heatmap' },
  { mode: 'both', label: 'Both' },
  { mode: 'plain', label: 'Hide' },
];

/**
 * Circular reticle + crosshair + labeled callout per detected lesion,
 * positioned from the REAL centroid/bounding-box data already in the
 * API response (LesionMeasurement.centroidNormalized/
 * boundingBoxNormalized) — no backend change needed, this is purely a
 * client-side vector overlay on top of whichever base image is showing.
 *
 * The label uses ONLY this project's own computed location text
 * (locationDescription, e.g. "centrally located" / "left frontal
 * region") — never a specific anatomical subregion name (e.g. "sellar/
 * suprasellar") unless location_description actually produces that,
 * since this pipeline does not do fine-grained anatomical subregion
 * classification and claiming otherwise would overstate precision.
 */
function LesionMarkers({ lesions }: { lesions: LesionMeasurement[] }) {
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 w-full h-full pointer-events-none">
      {lesions.map((l) => {
        const [cx, cy] = l.centroidNormalized;
        const [x0, y0, x1, y1] = l.boundingBoxNormalized;
        const rx = ((x1 - x0) / 2) * 100;
        const ry = ((y1 - y0) / 2) * 100;
        const r = Math.max((rx + ry) / 2, 6);
        const cxPct = cx * 100;
        const cyPct = cy * 100;
        return (
          <g key={l.lesionId}>
            <circle
              cx={cxPct}
              cy={cyPct}
              r={r}
              fill="rgba(34,211,176,0.10)"
              stroke="#22d3b0"
              strokeWidth="0.6"
              strokeDasharray="2,1.4"
            />
            <line x1={cxPct - r - 3} y1={cyPct} x2={cxPct + r + 3} y2={cyPct} stroke="#22d3b0" strokeWidth="0.3" opacity="0.75" />
            <line x1={cxPct} y1={cyPct - r - 3} x2={cxPct} y2={cyPct + r + 3} stroke="#22d3b0" strokeWidth="0.3" opacity="0.75" />
            <circle cx={cxPct} cy={cyPct} r="0.8" fill="#22d3b0" />
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Displays the fused-attention overlay PNG served by the backend
 * (Grad-CAM + saliency + edges, thresholded into a clean boundary — see
 * app/models/gradcam.py + weak_localization.py). Not available until
 * weights are loaded AND analysis has completed — the backend returns
 * 404 in that case, which this component treats as an expected,
 * honestly-labeled empty state rather than an error.
 *
 * Region/Heatmap/Both/Hide toggle switches between 4 backend-rendered
 * variants of the SAME underlying data (no re-computation per click —
 * see inference.py's _render_variant, generated once at analysis time)
 * so switching is just picking a different cached image URL: instant,
 * no loading spinner needed for the common case. The circular marker +
 * label overlay is drawn client-side on top, independent of which base
 * image variant is selected (except 'plain'/Hide, where it's hidden too).
 */
export function GradCamPanel({
  studyId,
  imageId,
  weakLocalization,
}: {
  studyId: string;
  imageId?: string | null;
  weakLocalization?: WeakLocalization | null;
}) {
  const [failed, setFailed] = useState(false);
  const [mode, setMode] = useState<GradcamMode>('both');
  const [url, setUrl] = useState<string | null>(null);

  // Authenticated fetch -> blob: URL (a bare <img src> can't carry the
  // bearer token the backend now requires).
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setFailed(false);
    setUrl(null);
    fetchGradcamObjectUrl(studyId, imageId ?? undefined, mode)
      .then((u) => {
        objectUrl = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setUrl(u);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [studyId, imageId, mode]);

  // Reset to the default combined view whenever the underlying image
  // changes (switching between per-image thumbnails in a multi-image
  // study) — otherwise "Hide" chosen on image A would silently carry
  // over and look broken on image B.
  useEffect(() => {
    setMode('both');
  }, [studyId, imageId]);

  const hasToggle = !!weakLocalization; // no-tumour case: all 4 modes are identical, toggle would do nothing
  const showMarkers = mode !== 'plain' && weakLocalization && weakLocalization.lesions.length > 0;

  return (
    <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-3">
      <div className="text-xs uppercase tracking-wider text-text3 font-medium">
        Model attention &amp; lesion measurements
      </div>

      {!failed ? (
        <div className="relative">
          <div className="relative rounded-rad overflow-hidden border border-border2 bg-black">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {url ? (
            <img
              src={url}
              alt={weakLocalization ? 'Fused attention heatmap with region boundary' : 'Analyzed image (no tumour attention to show)'}
              className="w-full aspect-square object-contain"
              onError={() => setFailed(true)}
            />
            ) : (
              <div className="w-full aspect-square flex items-center justify-center text-xs text-white/60">Loading…</div>
            )}
            {showMarkers && <LesionMarkers lesions={weakLocalization!.lesions} />}

            {hasToggle && (
              <div className="absolute bottom-2 left-2 flex gap-1 bg-black/60 backdrop-blur-sm rounded-rad p-1 z-20">
                {MODE_OPTIONS.map((opt) => (
                  <button
                    key={opt.mode}
                    onClick={() => setMode(opt.mode)}
                    className={
                      'text-[0.7rem] px-2.5 py-1 rounded transition-colors ' +
                      (mode === opt.mode
                        ? opt.mode === 'plain'
                          ? 'bg-glioma/80 text-white'
                          : 'bg-accent text-white'
                        : 'text-white/70 hover:text-white hover:bg-white/10')
                    }
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Labels render in this OUTER, non-clipped wrapper (same
              bounding box as the inner image, since border-box sizing
              means the border above doesn't change it) so a label near
              the edge isn't cut off by the image's rounded-corner clip. */}
          {showMarkers && (
            <div className="absolute inset-0 pointer-events-none">
              {weakLocalization!.lesions.map((l) => {
                const [cx, cy] = l.centroidNormalized;
                const flipAbove = cy > 0.62;
                return (
                  <div
                    key={`label-${l.lesionId}`}
                    className="absolute pointer-events-none z-10"
                    style={{
                      left: `${Math.min(Math.max(cx * 100, 18), 82)}%`,
                      top: flipAbove ? undefined : `${cy * 100}%`,
                      bottom: flipAbove ? `${(1 - cy) * 100}%` : undefined,
                      transform: `translate(-50%, ${flipAbove ? '-10px' : '10px'})`,
                    }}
                  >
                    <div className="bg-bg2/95 border border-accent2/50 rounded-rad px-2.5 py-1.5 text-[0.68rem] text-text whitespace-nowrap shadow-lg">
                      <span className="text-accent2 font-medium">{l.lesionId}</span> · {l.locationDescription} — model focus
                      <div className="text-text3 font-mono text-[0.62rem] mt-0.5">
                        ⌀ {l.maxDiameterMm} mm · {l.areaCm2} cm²
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-rad border border-border2 bg-bg3 py-10 text-center text-sm text-text3">
          No image available yet — requires trained weights loaded and a
          completed analysis run.
        </div>
      )}

      {mode === 'plain' && hasToggle && (
        <p className="text-xs text-text3">
          Overlay hidden — showing the plain analyzed image. Click Region, Heatmap, or Both above to
          bring it back.
        </p>
      )}

      {!weakLocalization && !failed && (
        <p className="text-xs text-text3">
          No tumour predicted, so no attention overlay is shown — plain image above confirms what was
          analyzed, without implying a finding that isn't there.
        </p>
      )}

      {weakLocalization && (
        <div className="bg-bg3 border border-border rounded-rad p-3.5 flex flex-col gap-3">
          {weakLocalization.lesions.length > 0 ? (
            <>
              <div className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">
                {weakLocalization.lesions.length} region{weakLocalization.lesions.length > 1 ? 's' : ''} detected
              </div>
              <div className="flex flex-col gap-2">
                {weakLocalization.lesions.map((l) => (
                  <div key={l.lesionId} className="grid grid-cols-[32px_1fr_auto] gap-2 items-baseline text-sm">
                    <span className="font-mono text-accent2">{l.lesionId}</span>
                    <span className="text-text2 text-xs">
                      {l.locationDescription} · {l.widthMm}×{l.heightMm} mm
                      {l.volumeCm3Estimate !== null ? ` · ~${l.volumeCm3Estimate} cm³ (est.)` : ''}
                      {l.boundaryRefined ? (
                        <span className="text-accent2"> · contrast-refined</span>
                      ) : (
                        <span className="text-warn"> · raw attention only</span>
                      )}
                    </span>
                    <span className="font-mono text-xs text-text whitespace-nowrap">
                      ⌀ {l.maxDiameterMm} mm · {l.areaCm2} cm²
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <span className={`text-sm ${weakLocalization.isDiffuse ? 'text-warn' : 'text-text3'}`}>
              {weakLocalization.locationDescription}
            </span>
          )}
          <p className="text-[0.68rem] text-warn leading-relaxed pt-1 border-t border-border">
            {weakLocalization.methodNote}
          </p>
        </div>
      )}

      {weakLocalization && (
        <p className="text-[0.7rem] text-text3 leading-relaxed">
          Heatmap computed via real backpropagation (Grad-CAM) fused with
          pixel saliency and edge structure — not a simulated overlay, see{' '}
          <code className="font-mono">app/models/gradcam.py</code>. Each white
          boundary marks a distinct region, labeled L1/L2/... by size.
          "Contrast-refined" regions were snapped from the coarse attention
          area to real image intensity structure via GrabCut (see{' '}
          <code className="font-mono">app/models/segmentation_refine.py</code>) —
          closer to the actual tissue boundary than raw attention alone, but
          still not a trained or validated segmentation. Red = strongest
          model contribution; blue = minimal. Reflects model attention, not
          confirmed pathology.
        </p>
      )}
    </div>
  );
}
