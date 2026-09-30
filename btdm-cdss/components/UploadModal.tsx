'use client';

import { useState } from 'react';
import { uploadStudy } from '@/lib/api';
import type { Study } from '@/lib/types';

export function UploadModal({
  onClose,
  onUploaded,
}: {
  onClose: () => void;
  onUploaded: (study: Study) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [patientAlias, setPatientAlias] = useState('');
  const [sequences, setSequences] = useState('T1');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadedStudy, setUploadedStudy] = useState<Study | null>(null);

  const canSubmit = files.length > 0 && patientAlias.trim().length > 0 && !uploading;

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFiles(Array.from(e.target.files ?? []));
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const study = await uploadStudy({ files, patientAlias: patientAlias.trim(), sequences });
      if (study.skippedFileCount > 0) {
        // Show what got filtered out before navigating away — a real
        // full-study export always mixes non-image objects
        // (presentation states, structured reports, scout localizers)
        // in with the actual scans, and silently discarding them without
        // saying so would be confusing ("I uploaded 52 files, why does
        // it only show 39?").
        setUploadedStudy(study);
      } else {
        onUploaded(study);
        onClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed — check the backend is running.');
    } finally {
      setUploading(false);
    }
  };

  if (uploadedStudy) {
    return (
      <div className="fixed inset-0 bg-black/60 z-[300] flex items-center justify-center p-6">
        <div className="bg-bg2 border border-border2 rounded-rad2 p-6 w-full max-w-md flex flex-col gap-4">
          <h2 className="font-serif text-xl tracking-tight">Upload complete</h2>
          <div className="bg-bg3 border border-border rounded-rad p-4 flex flex-col gap-2">
            <div className="text-sm text-text">
              <span className="text-normal font-medium">{uploadedStudy.imageIds.length}</span> image
              {uploadedStudy.imageIds.length !== 1 ? 's' : ''} accepted
              {uploadedStudy.skippedFileCount > 0 && (
                <>
                  , <span className="text-warn font-medium">{uploadedStudy.skippedFileCount}</span> skipped
                </>
              )}
            </div>
            {uploadedStudy.skippedFileCount > 0 && (
              <>
                <p className="text-xs text-text3">
                  Skipped files had no image data to analyze (presentation states, structured
                  reports, scout summaries, or similar non-scan objects — normal in a full study
                  export):
                </p>
                <div className="max-h-32 overflow-y-auto flex flex-col gap-1">
                  {uploadedStudy.skipReasons.map((reason, i) => (
                    <div key={i} className="text-[0.7rem] text-text3 font-mono truncate">
                      {reason}
                    </div>
                  ))}
                  {uploadedStudy.skippedFileCount > uploadedStudy.skipReasons.length && (
                    <div className="text-[0.7rem] text-text3">
                      …and {uploadedStudy.skippedFileCount - uploadedStudy.skipReasons.length} more
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
          <button
            onClick={() => {
              onUploaded(uploadedStudy);
              onClose();
            }}
            className="bg-accent hover:bg-[#2563eb] text-white text-sm font-medium rounded-rad px-5 py-2.5 transition-colors self-end"
          >
            View study
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 bg-black/60 z-[300] flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-bg2 border border-border2 rounded-rad2 p-6 w-full max-w-md flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-xl tracking-tight">Upload MRI study</h2>
          <button onClick={onClose} className="text-text3 hover:text-text text-lg px-1">
            ✕
          </button>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-text2">
            Image file(s) — PNG, JPG, DICOM, or a .zip of a full study (subfolders are fine —
            non-image files like presentation states/reports are automatically filtered out).
          </span>
          <input
            type="file"
            accept="image/*,.dcm,.zip"
            multiple
            onChange={handleFileChange}
            className="text-sm text-text2 file:mr-3 file:py-1.5 file:px-3 file:rounded-rad file:border-0 file:bg-accent/15 file:text-accent2 file:text-xs file:cursor-pointer"
          />
        </label>

        {files.length > 0 && (
          <div className="flex flex-col gap-1.5 max-h-32 overflow-y-auto">
            {files.map((f, i) => (
              <div
                key={`${f.name}-${i}`}
                className="flex items-center justify-between bg-bg3 border border-border rounded-rad px-3 py-1.5 text-xs"
              >
                <span className="text-text2 truncate">{f.name}</span>
                <button
                  onClick={() => removeFile(i)}
                  className="text-text3 hover:text-[#fca5a5] ml-2 flex-shrink-0"
                >
                  ✕
                </button>
              </div>
            ))}
            <span className="text-[0.68rem] text-text3">
              {files.length} file{files.length > 1 ? 's' : ''} selected
              {files.length > 1 ? ' — will be analyzed individually, then combined into a consensus.' : ''}
            </span>
          </div>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-text2">Patient alias (de-identified — never a real name/MRN)</span>
          <input
            type="text"
            value={patientAlias}
            onChange={(e) => setPatientAlias(e.target.value)}
            placeholder="PT-ALIAS-0001"
            className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-text2">Sequences (comma-separated)</span>
          <input
            type="text"
            value={sequences}
            onChange={(e) => setSequences(e.target.value)}
            placeholder="T1c, T2, FLAIR"
            className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none"
          />
        </label>

        {error && <div className="text-xs text-glioma bg-glioma/10 border border-glioma/25 rounded-rad p-2.5">{error}</div>}

        <div className="flex gap-2 justify-end pt-1">
          <button
            onClick={onClose}
            className="text-sm text-text2 hover:text-text border border-border2 rounded-rad px-4 py-2"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded-rad px-5 py-2 transition-colors"
          >
            {uploading ? 'Uploading…' : `Upload${files.length > 1 ? ` ${files.length} files` : ''}`}
          </button>
        </div>
      </div>
    </div>
  );
}
