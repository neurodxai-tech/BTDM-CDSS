'use client';

import { useState } from 'react';
import type { ClinicalReport } from '@/lib/types';
import { useAuth } from '@/lib/auth';
import { RoleGuard } from './RoleGuard';
import { signReport, updateReport, getReportDraft } from '@/lib/api';

export function ReportPanel({
  report,
  onSigned,
}: {
  report: ClinicalReport;
  onSigned: (r: ClinicalReport) => void;
}) {
  const { user, token } = useAuth();
  const [signing, setSigning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [clinicalIndication, setClinicalIndication] = useState(report.clinicalIndication);
  const [impression, setImpression] = useState(report.impression);
  const [recommendation, setRecommendation] = useState(report.recommendation);
  const [dirty, setDirty] = useState(false);

  const isSigned = report.status === 'signed';

  const handleGenerateDraft = async () => {
    if (!token) return;
    setGenerating(true);
    setError(null);
    try {
      const draft = await getReportDraft(report.studyId, token);
      // Populates the editable fields only — does NOT save or sign
      // anything. The radiologist reviews/edits this exactly like text
      // they'd typed themselves, then clicks Save/Sign as normal.
      setClinicalIndication(draft.clinicalIndication);
      setImpression(draft.impression);
      setDirty(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate draft.');
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    if (!token) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateReport(report.studyId, token, {
        clinicalIndication,
        impression,
        recommendation,
      });
      onSigned(updated);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  const handleSign = async () => {
    if (!user || !token) return;
    setError(null);
    // Save any unsaved edits first so signing doesn't silently drop them.
    if (dirty) {
      await handleSave();
    }
    setSigning(true);
    try {
      const signed = await signReport(report.studyId, token);
      onSigned(signed);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sign — check that an impression is filled in.');
    } finally {
      setSigning(false);
    }
  };

  const markDirty = () => setDirty(true);

  return (
    <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-text3 font-medium">MR Report</div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => window.open(`/print/${report.studyId}`, '_blank')}
            className="text-xs text-text2 hover:text-text border border-border2 rounded-rad px-3 py-1 transition-colors flex items-center gap-1.5"
            title="Open a print-friendly version in a new tab"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z" />
            </svg>
            Print report
          </button>
          <span
            className={
              'text-xs font-mono px-2.5 py-1 rounded-full border ' +
              (isSigned
                ? 'bg-normal/15 text-normal border-normal/25'
                : 'bg-warn/15 text-warn border-warn/25')
            }
          >
            {isSigned ? 'Signed — final' : 'Preliminary — unsigned'}
          </span>
        </div>
      </div>

      {error && (
        <div className="text-xs text-[#fca5a5] bg-glioma/10 border border-glioma/25 rounded-rad p-2.5">
          {error}
        </div>
      )}

      {/* Technique + Findings — always read-only, machine-generated */}
      <ReportSection label="Technique" text={report.technique} />
      <ReportSection label="Findings (AI-assisted)" text={report.findings} />

      {/* Clinical indication, Impression, Recommendation — radiologist-owned */}
      {isSigned ? (
        <>
          <ReportSection label="Clinical indication" text={report.clinicalIndication || '—'} />
          <ReportSection label="Impression" text={report.impression} emphasized />
          <ReportSection label="Recommendation" text={report.recommendation || '—'} />
          <div className="text-xs text-text3 border-t border-border pt-3">
            Signed by <span className="text-text">{report.signedBy}</span> ·{' '}
            {report.signedAt && new Date(report.signedAt).toLocaleString()}
          </div>
        </>
      ) : (
        <RoleGuard
          permission="sign_report"
          fallback={
            <>
              <ReportSection label="Clinical indication" text={report.clinicalIndication || '—'} />
              <ReportSection label="Impression" text={report.impression || 'Not yet written by radiologist.'} />
              <ReportSection label="Recommendation" text={report.recommendation || '—'} />
              <div className="text-xs text-text3 border-t border-border pt-3">
                Awaiting radiologist review and sign-off. This report is not final.
              </div>
            </>
          }
        >
          <div className="flex items-center justify-between -mb-1">
            <span className="text-[0.68rem] text-text3">
              Start from an AI-drafted suggestion, or write your own from scratch below.
            </span>
            <button
              onClick={handleGenerateDraft}
              disabled={generating}
              className="text-xs text-accent2 border border-accent/30 rounded-rad px-3 py-1.5 hover:bg-accent/10 disabled:opacity-40 transition-colors flex-shrink-0"
            >
              {generating ? 'Generating…' : '✨ Generate draft'}
            </button>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">
              Clinical indication
            </span>
            <textarea
              value={clinicalIndication}
              onChange={(e) => {
                setClinicalIndication(e.target.value);
                markDirty();
              }}
              placeholder="Reason the study was ordered…"
              rows={2}
              className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none resize-none"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">
              Impression <span className="text-glioma">*required to sign</span>
            </span>
            <textarea
              value={impression}
              onChange={(e) => {
                setImpression(e.target.value);
                markDirty();
              }}
              placeholder="Your clinical conclusion — this is what actually gets signed, not the AI findings above…"
              rows={6}
              className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none resize-none font-mono"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">
              Recommendation
            </span>
            <textarea
              value={recommendation}
              onChange={(e) => {
                setRecommendation(e.target.value);
                markDirty();
              }}
              placeholder="Follow-up, referral, further imaging…"
              rows={2}
              className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none resize-none"
            />
          </label>

          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className="text-sm text-accent2 border border-accent/30 rounded-rad px-4 py-2 hover:bg-accent/10 disabled:opacity-40 transition-colors"
            >
              {saving ? 'Saving…' : 'Save draft'}
            </button>
            <button
              onClick={handleSign}
              disabled={signing || !impression.trim()}
              className="bg-accent hover:bg-[#2563eb] disabled:opacity-40 text-white text-sm font-medium rounded-rad px-5 py-2 transition-colors"
            >
              {signing ? 'Signing…' : `Sign as ${user?.name}`}
            </button>
          </div>
        </RoleGuard>
      )}
    </div>
  );
}

function ReportSection({ label, text, emphasized }: { label: string; text: string; emphasized?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[0.68rem] uppercase tracking-wider text-text3 font-medium">{label}</span>
      <p className={emphasized ? 'text-sm text-text leading-relaxed' : 'text-sm text-text2 leading-relaxed'}>
        {text}
      </p>
    </div>
  );
}
