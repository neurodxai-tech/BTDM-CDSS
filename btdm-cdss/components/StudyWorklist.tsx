'use client';

import Link from 'next/link';
import { useState } from 'react';
import type { Study } from '@/lib/types';
import { useAuth } from '@/lib/auth';
import { RoleGuard } from './RoleGuard';
import { deleteStudy } from '@/lib/api';

const STATUS_LABEL: Record<Study['status'], { label: string; color: string }> = {
  uploaded: { label: 'Uploaded', color: 'text-text2' },
  preprocessing: { label: 'Preprocessing', color: 'text-warn' },
  analyzing: { label: 'Analyzing', color: 'text-accent2' },
  analyzed: { label: 'Analyzed', color: 'text-accent2' },
  report_pending_signoff: { label: 'Pending sign-off', color: 'text-warn' },
  report_signed: { label: 'Signed', color: 'text-normal' },
  failed: { label: 'Analysis failed', color: 'text-glioma' },
};

export function StudyWorklist({
  studies,
  onDeleted,
}: {
  studies: Study[];
  onDeleted?: (studyId: string) => void;
}) {
  const { user, token } = useAuth();
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async (studyId: string) => {
    if (!user || !token) return;
    setDeletingId(studyId);
    setError(null);
    try {
      await deleteStudy(studyId, token);
      onDeleted?.(studyId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setDeletingId(null);
      setConfirmingId(null);
    }
  };

  return (
    <div className="bg-bg2 border border-border rounded-rad2 overflow-hidden">
      {error && (
        <div className="px-5 py-2.5 text-xs text-[#fca5a5] bg-glioma/10 border-b border-glioma/25">
          {error}
        </div>
      )}
      <div className="grid grid-cols-[1fr_140px_160px_160px_120px_90px] gap-2 px-5 py-3 text-[0.68rem] uppercase tracking-wider text-text3 border-b border-border2 font-medium">
        <span>Patient (de-identified)</span>
        <span>Modality</span>
        <span>Sequences</span>
        <span>Uploaded</span>
        <span>Status</span>
        <span></span>
      </div>
      {studies.map((s) => {
        const status = STATUS_LABEL[s.status];
        const isConfirming = confirmingId === s.id;
        const isDeleting = deletingId === s.id;
        return (
          <div
            key={s.id}
            className="grid grid-cols-[1fr_140px_160px_160px_120px_90px] gap-2 px-5 py-3.5 text-sm border-b border-border last:border-b-0 hover:bg-bg3 transition-colors items-center"
          >
            <Link href={`/studies/${s.id}`} className="font-mono text-text hover:text-accent2">
              {s.patientAlias}
            </Link>
            <Link href={`/studies/${s.id}`} className="text-text2">{s.modality}</Link>
            <Link href={`/studies/${s.id}`} className="text-text2">{s.sequences.join(' · ')}</Link>
            <Link href={`/studies/${s.id}`} className="text-text3">
              {new Date(s.uploadedAt).toLocaleString()}
            </Link>
            <Link href={`/studies/${s.id}`} className={status.color}>{status.label}</Link>

            <RoleGuard permission="delete_study">
              {isConfirming ? (
                <div className="flex gap-1.5 justify-end">
                  <button
                    onClick={() => handleDelete(s.id)}
                    disabled={isDeleting}
                    className="text-[0.7rem] bg-glioma/20 text-[#fca5a5] border border-glioma/40 rounded px-2 py-1 hover:bg-glioma/30 disabled:opacity-50"
                  >
                    {isDeleting ? '…' : 'Confirm'}
                  </button>
                  <button
                    onClick={() => setConfirmingId(null)}
                    className="text-[0.7rem] text-text3 border border-border2 rounded px-2 py-1 hover:text-text"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmingId(s.id)}
                  className="text-[0.7rem] text-text3 hover:text-[#fca5a5] justify-self-end px-2 py-1"
                  title="Delete study"
                >
                  Delete
                </button>
              )}
            </RoleGuard>
          </div>
        );
      })}
      {studies.length === 0 && (
        <div className="px-5 py-10 text-center text-text3 text-sm">
          No studies yet. Upload an MRI study to begin.
        </div>
      )}
    </div>
  );
}
