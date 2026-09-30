'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { listStudies } from '@/lib/api';
import type { Study } from '@/lib/types';
import { StudyWorklist } from '@/components/StudyWorklist';
import { RoleGuard } from '@/components/RoleGuard';
import { UploadModal } from '@/components/UploadModal';
import { useAuth } from '@/lib/auth';

export default function StudiesPage() {
  const [studies, setStudies] = useState<Study[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const router = useRouter();
  const { user } = useAuth();

  // Admins manage users and models only; send them to the Admin panel.
  useEffect(() => {
    if (user?.role === 'admin') router.replace('/admin');
  }, [user, router]);

  const load = () => {
    setLoadError(null);
    setStudies(null);
    listStudies()
      .then(setStudies)
      .catch((err) => {
        setLoadError(
          err instanceof Error
            ? err.message
            : 'Could not reach the backend. Is it running at NEXT_PUBLIC_API_BASE_URL?'
        );
      });
  };

  useEffect(() => {
    load();
  }, []);

  const handleUploaded = (study: Study) => {
    router.push(`/studies/${study.id}`);
  };

  const handleDeleted = (studyId: string) => {
    setStudies((prev) => (prev ? prev.filter((s) => s.id !== studyId) : prev));
  };

  return (
    <div className="flex flex-col gap-5 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-serif text-2xl tracking-tight">My worklist</h1>
          <p className="text-xs text-text3 mt-1">
            {user ? `${user.name} · ` : ''}only studies you uploaded are shown. Every doctor&apos;s
            analyses run on the same shared AI model.
          </p>
        </div>
        <RoleGuard permission="upload_study">
          <button
            onClick={() => setShowUpload(true)}
            className="bg-accent hover:bg-[#2563eb] text-white rounded-rad px-5 py-2.5 text-sm font-medium transition-colors"
          >
            + Upload study
          </button>
        </RoleGuard>
      </div>

      {loadError ? (
        <div className="bg-glioma/10 border border-glioma/25 rounded-rad2 p-5 flex flex-col gap-3">
          <div className="text-sm text-[#fca5a5]">
            <strong>Could not load studies.</strong> {loadError}
          </div>
          <div className="text-xs text-text3">
            Check that the backend is running and reachable at the URL set in
            <code className="font-mono mx-1">NEXT_PUBLIC_API_BASE_URL</code>
            (see browser console / Network tab for the exact request that failed).
          </div>
          <button
            onClick={load}
            className="self-start text-xs text-accent2 border border-accent/30 rounded-rad px-3 py-1.5 hover:bg-accent/10"
          >
            &#8635; Retry
          </button>
        </div>
      ) : studies === null ? (
        <div className="text-text3 text-sm">Loading studies…</div>
      ) : (
        <StudyWorklist studies={studies} onDeleted={handleDeleted} />
      )}

      {showUpload && <UploadModal onClose={() => setShowUpload(false)} onUploaded={handleUploaded} />}
    </div>
  );
}
