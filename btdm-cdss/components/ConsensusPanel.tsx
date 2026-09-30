'use client';

import { useRef, useState } from 'react';
import type { ConsensusInfo, PerImageResult } from '@/lib/types';
import { fetchGradcam } from '@/lib/api';

const CLASS_META: Record<string, { label: string; color: string }> = {
  glioma: { label: 'Glioma', color: '#e85d5d' },
  meningioma: { label: 'Meningioma', color: '#a855f7' },
  pituitary: { label: 'Pituitary', color: '#22d3b0' },
  notumor: { label: 'No tumour', color: '#4ade80' },
};

/**
 * Shown only for multi-image studies (consensus/perImageResults are null
 * for single-image studies — see backend services/inference.py). Makes
 * the majority vote and any disagreement across slices/views visible,
 * plus lets the radiologist select several images (checkbox, drag-to-
 * select, or "select all of class X") and download them bundled into
 * one ZIP, organized into a folder per predicted class.
 *
 * No new backend endpoint needed for this — every image's analyzed PNG
 * (attention overlay, or plain image for no-tumour) is already served
 * by the existing /gradcam?image_id=... endpoint from the multi-lesion
 * work earlier; this just fetches each selected one and bundles them
 * client-side with JSZip.
 */
export function ConsensusPanel({
  studyId,
  consensus,
  perImageResults,
  primaryImageId,
  selectedImageId,
  onSelectImage,
}: {
  studyId: string;
  consensus: ConsensusInfo;
  perImageResults: PerImageResult[];
  primaryImageId: string | null;
  selectedImageId: string | null;
  onSelectImage: (imageId: string) => void;
}) {
  const reliabilityColor =
    consensus.reliabilityScore >= 0.7 ? '#4ade80' : consensus.reliabilityScore >= 0.4 ? '#f59e0b' : '#e85d5d';

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const gridRef = useRef<HTMLDivElement>(null);
  const [dragRect, setDragRect] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);

  const toggleOne = (imageId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(imageId)) next.delete(imageId);
      else next.add(imageId);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(perImageResults.map((r) => r.imageId)));
  const clearSelection = () => setSelected(new Set());
  const selectClass = (cls: string) =>
    setSelected(new Set(perImageResults.filter((r) => r.predictedClass === cls).map((r) => r.imageId)));

  // Drag-to-select: mousedown on empty grid space starts a rectangle;
  // any card whose bounding box intersects it on mouseup gets added to
  // the selection (additive — doesn't clear existing checkbox picks).
  const handleGridMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('[data-card]')) return; // let card clicks behave normally
    const rect = gridRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragStart.current = { x: e.clientX, y: e.clientY };
    setDragRect({ x0: e.clientX, y0: e.clientY, x1: e.clientX, y1: e.clientY });
  };

  const handleGridMouseMove = (e: React.MouseEvent) => {
    if (!dragStart.current) return;
    setDragRect({ x0: dragStart.current.x, y0: dragStart.current.y, x1: e.clientX, y1: e.clientY });
  };

  const handleGridMouseUp = () => {
    if (!dragStart.current || !dragRect) {
      dragStart.current = null;
      setDragRect(null);
      return;
    }
    const left = Math.min(dragRect.x0, dragRect.x1);
    const right = Math.max(dragRect.x0, dragRect.x1);
    const top = Math.min(dragRect.y0, dragRect.y1);
    const bottom = Math.max(dragRect.y0, dragRect.y1);

    // Ignore near-zero-area drags (i.e. a plain click that didn't move) —
    // those should just be a normal click, not a selection gesture.
    if (right - left > 4 || bottom - top > 4) {
      setSelected((prev) => {
        const next = new Set(prev);
        cardRefs.current.forEach((el, imageId) => {
          const r = el.getBoundingClientRect();
          const intersects = r.left < right && r.right > left && r.top < bottom && r.bottom > top;
          if (intersects) next.add(imageId);
        });
        return next;
      });
    }
    dragStart.current = null;
    setDragRect(null);
  };

  const handleDownload = async () => {
    if (selected.size === 0) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const JSZip = (await import('jszip')).default;
      const zip = new JSZip();
      const byId = new Map(perImageResults.map((r) => [r.imageId, r]));

      const results = await Promise.allSettled(
        Array.from(selected).map(async (imageId) => {
          const res = await fetchGradcam(studyId, imageId).catch(() => {
            throw new Error(imageId);
          });
          const blob = await res.blob();
          const cls = byId.get(imageId)?.predictedClass ?? 'unknown';
          zip.folder(cls)?.file(`${imageId}.png`, blob);
        })
      );

      const failed = results.filter((r) => r.status === 'rejected').length;
      if (failed > 0) {
        setDownloadError(`${failed} of ${selected.size} image(s) failed to download — the rest are included below.`);
      }

      const blob = await zip.generateAsync({ type: 'blob' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${studyId}-images-by-class.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed.');
    } finally {
      setDownloading(false);
    }
  };

  const presentClasses = Array.from(new Set(perImageResults.map((r) => r.predictedClass)));

  return (
    <div className="bg-bg2 border border-border2 rounded-rad2 p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-text3 font-medium">
          Multi-image consensus — {consensus.numImages} images
        </div>
        <span
          className="text-xs font-mono px-2.5 py-1 rounded-full border"
          style={{ color: reliabilityColor, borderColor: `${reliabilityColor}40`, background: `${reliabilityColor}15` }}
        >
          Reliability {(consensus.reliabilityScore * 100).toFixed(0)}%
        </span>
      </div>

      <p className="text-sm text-text2 leading-relaxed">{consensus.reliabilityReason}</p>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">
            Per-image results — click to view, checkbox or drag to select for download
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <button onClick={selectAll} className="text-[0.68rem] text-text3 hover:text-text border border-border2 rounded px-2 py-0.5">
              Select all
            </button>
            {presentClasses.map((cls) => (
              <button
                key={cls}
                onClick={() => selectClass(cls)}
                className="text-[0.68rem] text-text3 hover:text-text border border-border2 rounded px-2 py-0.5"
              >
                {CLASS_META[cls]?.label ?? cls}
              </button>
            ))}
            {selected.size > 0 && (
              <button onClick={clearSelection} className="text-[0.68rem] text-text3 hover:text-text border border-border2 rounded px-2 py-0.5">
                Clear ({selected.size})
              </button>
            )}
          </div>
        </div>

        <div
          ref={gridRef}
          onMouseDown={handleGridMouseDown}
          onMouseMove={handleGridMouseMove}
          onMouseUp={handleGridMouseUp}
          className="grid grid-cols-2 sm:grid-cols-3 gap-2 relative select-none"
        >
          {dragRect && (
            <div
              className="fixed border border-accent bg-accent/10 pointer-events-none z-50"
              style={{
                left: Math.min(dragRect.x0, dragRect.x1),
                top: Math.min(dragRect.y0, dragRect.y1),
                width: Math.abs(dragRect.x1 - dragRect.x0),
                height: Math.abs(dragRect.y1 - dragRect.y0),
              }}
            />
          )}
          {perImageResults.map((r) => {
            const meta = CLASS_META[r.predictedClass] ?? { label: r.predictedClass, color: '#8b93a8' };
            const isViewing = r.imageId === selectedImageId;
            const isPrimary = r.imageId === primaryImageId;
            const isChecked = selected.has(r.imageId);
            return (
              <div
                key={r.imageId}
                data-card
                ref={(el) => {
                  if (el) cardRefs.current.set(r.imageId, el);
                  else cardRefs.current.delete(r.imageId);
                }}
                onClick={() => onSelectImage(r.imageId)}
                className={
                  'relative text-left rounded-rad border p-2.5 transition-colors cursor-pointer ' +
                  (isViewing ? 'border-accent bg-accent/10' : 'border-border hover:border-border2 bg-bg3') +
                  (isChecked ? ' ring-2 ring-accent2' : '')
                }
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => toggleOne(r.imageId)}
                  className="absolute top-1.5 right-1.5 w-3.5 h-3.5 accent-accent2 cursor-pointer"
                />
                <div className="flex items-center gap-1.5 pr-4">
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: meta.color }} />
                  <span className="text-xs text-text truncate">{meta.label}</span>
                  {isPrimary && (
                    <span className="text-[0.6rem] text-accent2 ml-auto flex-shrink-0">primary</span>
                  )}
                </div>
                <div className="font-mono text-[0.7rem] text-text3 mt-0.5">
                  {(r.calibratedConfidence * 100).toFixed(1)}%
                </div>
              </div>
            );
          })}
        </div>

        {selected.size > 0 && (
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleDownload}
              disabled={downloading}
              className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white text-xs font-medium rounded-rad px-4 py-2 transition-colors"
            >
              {downloading ? 'Zipping…' : `Download ${selected.size} image${selected.size > 1 ? 's' : ''} (.zip, by class)`}
            </button>
            {downloadError && <span className="text-[0.7rem] text-glioma">{downloadError}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
