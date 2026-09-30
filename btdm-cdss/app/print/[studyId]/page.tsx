'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { getStudy, getReport, getAnalysis } from '@/lib/api';
import type { AnalysisResult, ClinicalReport, Study } from '@/lib/types';

/**
 * Standalone print view — deliberately OUTSIDE the (dashboard) route
 * group, so it doesn't inherit the dark AppShell chrome. Printing a dark
 * background wastes toner and looks unprofessional on paper; this page
 * is plain white/black regardless of the app's normal theme.
 *
 * Opened in a new tab from ReportPanel's "Print report" button so the
 * radiologist's place in the main app isn't disturbed.
 */
export default function PrintReportPage() {
  const params = useParams<{ studyId: string }>();
  const studyId = params.studyId;
  const { token, loading } = useAuth();

  const [study, setStudy] = useState<Study | null>(null);
  const [report, setReport] = useState<ClinicalReport | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loading) return;
    if (!token) {
      setError('Not signed in — open this from the app (Print report button) rather than a bare link.');
      return;
    }
    Promise.all([getStudy(studyId), getReport(studyId), getAnalysis(studyId).catch(() => null)])
      .then(([s, r, a]) => {
        setStudy(s ?? null);
        setReport(r ?? null);
        setAnalysis(a ?? null);
      })
      .catch(() => setError('Could not load this report.'));
  }, [studyId, token, loading]);

  if (error) {
    return (
      <div className="min-h-screen bg-white text-black flex items-center justify-center p-8">
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  if (!study || !report) {
    return <div className="min-h-screen bg-white text-black flex items-center justify-center p-8 text-sm">Loading…</div>;
  }

  const isSigned = report.status === 'signed';

  return (
    <div className="min-h-screen bg-white text-black">
      {/* Print trigger + back link — hidden when actually printing */}
      <div className="print:hidden sticky top-0 bg-gray-100 border-b border-gray-300 px-6 py-3 flex items-center justify-between">
        <span className="text-sm text-gray-600">Print preview</span>
        <div className="flex gap-2">
          <button
            onClick={() => window.close()}
            className="text-sm text-gray-600 border border-gray-300 rounded px-4 py-1.5 hover:bg-gray-200"
          >
            Close
          </button>
          <button
            onClick={() => window.print()}
            className="text-sm bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5"
          >
            Print / Save as PDF
          </button>
        </div>
      </div>

      <div className="max-w-3xl mx-auto p-10 print:p-6">
        {/* Letterhead */}
        <div className="flex items-start justify-between border-b-2 border-black pb-4 mb-6">
          <div>
            <div className="font-serif text-2xl tracking-tight">BTDM-CDSS</div>
            <div className="text-xs text-gray-600">Brain Tumour Clinical Decision Support System</div>
          </div>
          <div className="text-right text-xs text-gray-600">
            <div>Report generated: {new Date().toLocaleString()}</div>
            <div>Study ID: {study.id}</div>
          </div>
        </div>

        <div className="border border-black rounded-none px-3 py-2 mb-6 text-xs font-medium">
          RESEARCH PROTOTYPE — NOT A CLEARED MEDICAL DEVICE. All AI-assisted findings require
          radiologist review and sign-off. Not for standalone clinical use.
        </div>

        {!isSigned && (
          <div className="border-2 border-red-600 text-red-700 px-3 py-2 mb-6 text-sm font-bold text-center">
            PRELIMINARY — UNSIGNED — NOT A FINAL REPORT
          </div>
        )}

        {/* Patient / study block */}
        <table className="w-full text-sm mb-6">
          <tbody>
            <Row label="Patient (de-identified)" value={study.patientAlias} />
            <Row label="Modality" value={`${study.modality} — ${study.sequences.join(', ')}`} />
            <Row label="Images submitted" value={String(study.imageIds.length)} />
            <Row label="Uploaded" value={new Date(study.uploadedAt).toLocaleString()} />
          </tbody>
        </table>

        <Section title="Clinical Indication" text={report.clinicalIndication || '— Not specified —'} />
        <Section title="Technique" text={report.technique} />
        <Section title="Findings (AI-Assisted)" text={report.findings} />
        <Section title="Impression" text={report.impression || '— Not yet completed —'} emphasized />
        <Section title="Recommendation" text={report.recommendation || '— None —'} />

        {analysis?.consensus && (
          <div className="text-xs text-gray-600 mb-6 border-t border-gray-300 pt-3">
            Multi-image consensus: {analysis.consensus.agreeingImages} of {analysis.consensus.numImages} images
            agreed ({(analysis.consensus.agreementFraction * 100).toFixed(0)}%), reliability score{' '}
            {(analysis.consensus.reliabilityScore * 100).toFixed(0)}%.
          </div>
        )}

        {/* Signature block */}
        <div className="mt-10 pt-6 border-t border-black">
          {isSigned ? (
            <div className="text-sm">
              <div className="font-medium">Electronically signed by: {report.signedBy}</div>
              <div className="text-gray-600">{report.signedAt && new Date(report.signedAt).toLocaleString()}</div>
            </div>
          ) : (
            <div className="text-sm text-gray-500">
              Not yet signed. This document has no clinical validity until reviewed and signed by a
              radiologist.
            </div>
          )}
        </div>

        <div className="mt-8 text-[0.65rem] text-gray-500 leading-relaxed border-t border-gray-300 pt-3">
          AI-assisted findings are generated by a research-stage classifier (see model card in the
          application) and are not independently validated for clinical accuracy. Lesion measurements,
          where present, are derived from model attention heuristics, not a trained or validated
          segmentation instrument, and should not be treated as precise anatomical measurements. This
          report is not a substitute for clinical judgment.
        </div>
      </div>

      {/* Print-specific page setup */}
      <style jsx global>{`
        @media print {
          @page {
            margin: 1.5cm;
          }
          body {
            background: white !important;
          }
        }
      `}</style>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td className="text-gray-600 py-1 pr-4 align-top whitespace-nowrap">{label}</td>
      <td className="py-1">{value}</td>
    </tr>
  );
}

function Section({ title, text, emphasized }: { title: string; text: string; emphasized?: boolean }) {
  return (
    <div className="mb-5">
      <div className="text-xs uppercase tracking-wide text-gray-500 font-semibold mb-1">{title}</div>
      <p className={emphasized ? 'text-sm font-medium leading-relaxed' : 'text-sm leading-relaxed'}>{text}</p>
    </div>
  );
}
