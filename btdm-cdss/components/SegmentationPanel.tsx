'use client';

import type { SegmentationVolumes } from '@/lib/types';

export function SegmentationPanel({ segmentation }: { segmentation: SegmentationVolumes }) {
  const hasSegmentation = segmentation.enhancingTumourCm3 !== null;

  return (
    <div className="bg-bg2 border border-border rounded-rad2 p-5">
      <div className="text-xs uppercase tracking-wider text-text3 font-medium mb-4">
        Lesion segmentation — volumetrics
      </div>
      {!hasSegmentation ? (
        <div className="text-sm text-text3">
          Not available in this release — the training notebook
          (BTDM_FINAL.ipynb) is a classification-only study with no
          segmentation head. This panel is wired for when a segmentation
          model is added (backend contract already supports it).
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          <VolStat label="Enhancing tumour" value={segmentation.enhancingTumourCm3} color="#e85d5d" />
          <VolStat label="Oedema" value={segmentation.edemaCm3} color="#f59e0b" />
          <VolStat label="Necrotic core" value={segmentation.necroticCoreCm3} color="#8b93a8" />
        </div>
      )}
      {hasSegmentation && (
        <div className="text-[0.7rem] text-text3 mt-4 pt-3 border-t border-border">
          Overlay mask is rendered directly in the DICOM viewer (segmentation layer toggle, top-right of the
          image panel) — the numbers here are computed voxel counts × voxel volume, not model confidence.
        </div>
      )}
    </div>
  );
}

function VolStat({ label, value, color }: { label: string; value: number | null; color: string }) {
  return (
    <div className="bg-bg3 border border-border rounded-rad p-3.5">
      <div className="text-[0.68rem] uppercase tracking-wider text-text3 mb-1">{label}</div>
      <div className="font-mono text-lg" style={{ color }}>
        {value !== null ? `${value.toFixed(1)} cm³` : '—'}
      </div>
    </div>
  );
}
