'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { getStudy, getAnalysis, getReport, triggerAnalysis, watchStudy, getJob } from '@/lib/api';
import type { StudyEvent } from '@/lib/types';
import type { AnalysisResult, ClinicalReport, Study } from '@/lib/types';
import { ClassificationPanel, ModelInfoFooter, PrimaryFindingBanner } from '@/components/ClassificationPanel';
import { SegmentationPanel } from '@/components/SegmentationPanel';
import { ReportPanel } from '@/components/ReportPanel';
import { GradCamPanel } from '@/components/GradCamPanel';
import { ConsensusPanel } from '@/components/ConsensusPanel';
import { ArchitectureCard } from '@/components/ArchitectureCard';
import { ModelPerformanceCard } from '@/components/ModelPerformanceCard';
import { ModelReferenceSection } from '@/components/ModelReferenceSection';
import { RoleGuard } from '@/components/RoleGuard';

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function StudyDetailPage() {
  const params = useParams<{ studyId: string }>();
  const studyId = params.studyId;

  const [study, setStudy] = useState<Study | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [report, setReport] = useState<ClinicalReport | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  const [studyLoading, setStudyLoading] = useState(true);
  const [studyLoadError, setStudyLoadError] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<StudyEvent['status'] | null>(null);
  const [progress, setProgress] = useState<string | null>(null);

  useEffect(() => {
    setStudyLoading(true);
    setStudyLoadError(null);
    getStudy(studyId)
      .then((s) => {
        if (!s) {
          setStudyLoadError('not_found');
        } else {
          setStudy(s);
          // Opened (or reloaded) while a job is queued/running: re-attach to
          // the live stream instead of making the user click anything.
          if (s.status === 'analyzing') {
            setAnalyzing(true);
            waitForCompletion()
              .catch((err) => setAnalyzeError(err instanceof Error ? err.message : String(err)))
              .finally(() => setAnalyzing(false));
          } else if (s.status === 'failed') {
            getJob(studyId)
              .then((j) => j?.error && setAnalyzeError(`Last analysis failed: ${j.error}`))
              .catch(() => {});
          }
        }
      })
      .catch(() => {
        // A 404 here almost always means the study was deleted (by anyone,
        // from any account) after this page was opened or linked to —
        // not a transient network blip. Treat it as "gone", not "retry forever".
        setStudyLoadError('not_found');
      })
      .finally(() => setStudyLoading(false));
    getAnalysis(studyId)
      .then((a) => {
        setAnalysis(a ?? null);
        setSelectedImageId(a?.primaryImageId ?? null);
      })
      .catch(() => setAnalysis(null));
    getReport(studyId).then((r) => setReport(r ?? null)).catch(() => setReport(null));
  }, [studyId]);

  const handleAnalyze = async () => {
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      await triggerAnalysis(studyId);
      setJobStatus('queued');
      await waitForCompletion();
    } catch (err) {
      setAnalyzeError(err instanceof Error ? err.message : 'Analysis failed — check the backend logs.');
    } finally {
      setAnalyzing(false);
    }
  };

  /**
   * Waits for the worker to finish. Primary path: a live Server-Sent Events
   * stream (status arrives the instant it changes). Fallback, if the stream
   * can't be opened or drops: poll the job record every 2 s. The job keeps
   * running server-side either way — leaving the page doesn't cancel it.
   */
  const waitForCompletion = async () => {
    let final: StudyEvent | null = null;
    try {
      await watchStudy(studyId, (e) => {
        if (e.type === 'progress' && e.message) setProgress(e.message);
        if (e.type === 'status') {
          setJobStatus(e.status ?? null);
          if (e.status === 'done' || e.status === 'failed') final = e;
        }
      });
    } catch {
      /* stream unavailable — fall through to polling */
    }

    for (let i = 0; !final && i < 450; i++) {
      const job = await getJob(studyId).catch(() => null);
      if (job) {
        setJobStatus(job.status);
        if (job.status === 'done' || job.status === 'failed') {
          final = { type: 'status', status: job.status, error: job.error };
          break;
        }
      }
      await sleep(2000);
    }
    setProgress(null);

    const outcome = final as StudyEvent | null;
    if (!outcome) {
      throw new Error('Still processing after 15 minutes. The job is still running on the server — use "Check again" later.');
    }
    if (outcome.status === 'failed') {
      throw new Error(`Analysis failed: ${outcome.error ?? 'see the worker log'}`);
    }

    const result = await getAnalysis(studyId);
    if (!result) throw new Error('Analysis finished but no result was stored — see the worker log.');
    setAnalysis(result);
    setSelectedImageId(result.primaryImageId);
    const newReport = await getReport(studyId).catch(() => null);
    if (newReport) setReport(newReport);
    const refreshed = await getStudy(studyId).catch(() => undefined);
    if (refreshed) setStudy(refreshed);
  };

  const handleCheckAgain = async () => {
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      const result = await getAnalysis(studyId);
      if (result) {
        setAnalysis(result);
        setSelectedImageId(result.primaryImageId);
        const newReport = await getReport(studyId).catch(() => null);
        if (newReport) setReport(newReport);
      } else {
        setAnalyzeError('Not ready yet — still processing. Try again in a moment.');
      }
    } catch {
      setAnalyzeError('Not ready yet — still processing. Try again in a moment.');
    } finally {
      setAnalyzing(false);
    }
  };

  if (studyLoading) {
    return <div className="text-text3 text-sm">Loading study…</div>;
  }

  if (studyLoadError || !study) {
    return (
      <div className="max-w-md mx-auto mt-16 bg-bg2 border border-border rounded-rad2 p-8 text-center flex flex-col gap-3">
        <div className="text-4xl">🗂️</div>
        <div className="text-text font-medium">This study no longer exists</div>
        <p className="text-sm text-text3">
          It may have been deleted by another user, or the link is stale.
        </p>
        <Link
          href="/studies"
          className="mt-2 self-center bg-accent hover:bg-[#2563eb] text-white text-sm font-medium rounded-rad px-5 py-2 transition-colors"
        >
          Back to worklist
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-serif text-2xl tracking-tight">{study.patientAlias}</h1>
          <p className="text-text3 text-sm mt-1">
            {study.modality} · {study.sequences.join(' · ')} · {study.imageIds.length} image
            {study.imageIds.length !== 1 ? 's' : ''} · uploaded{' '}
            {new Date(study.uploadedAt).toLocaleString()}
          </p>
        </div>
        <RoleGuard permission="run_inference">
          <div className="flex gap-2">
            {analyzeError && !analysis && (
              <button
                onClick={handleCheckAgain}
                disabled={analyzing}
                className="text-text2 hover:text-text border border-border2 disabled:opacity-50 rounded-rad px-4 py-2.5 text-sm font-medium transition-colors"
              >
                Check again
              </button>
            )}
            <button
              onClick={handleAnalyze}
              disabled={analyzing || study.status === 'report_signed'}
              title={study.status === 'report_signed' ? 'Report is signed — re-analysis is disabled' : undefined}
              className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white rounded-rad px-5 py-2.5 text-sm font-medium transition-colors"
            >
              {analyzing
                ? jobStatus === 'queued'
                  ? 'Queued…'
                  : progress ?? 'Analysing…'
                : analysis
                  ? 'Re-run analysis'
                  : 'Run analysis'}
            </button>
          </div>
        </RoleGuard>
      </div>

      {analyzeError && (
        <div className="bg-glioma/10 border border-glioma/25 rounded-rad p-4 text-sm text-[#fca5a5]">
          {analyzeError}
        </div>
      )}

      {analysis && <PrimaryFindingBanner result={analysis} />}

      <div className="grid grid-cols-1 lg:grid-cols-[1.4fr_1fr] gap-5">
        {/* LEFT — visual evidence: the image is the primary object of
            attention, with its own per-case quantitative support
            (multi-image agreement, segmentation volumes) directly
            beneath it, since both describe the SAME visual finding. */}
        <div className="flex flex-col gap-4">
          {analysis ? (
            <>
              <GradCamPanel
                studyId={study.id}
                imageId={selectedImageId}
                weakLocalization={
                  selectedImageId === analysis.primaryImageId ? analysis.weakLocalization : null
                }
              />
              {analysis.consensus && selectedImageId !== analysis.primaryImageId && (
                <p className="text-[0.7rem] text-text3 -mt-2">
                  Area/location text is only computed for the primary image — this is a
                  different image's attention map for comparison.
                </p>
              )}
              {analysis.consensus && analysis.perImageResults && (
                <ConsensusPanel
                  studyId={study.id}
                  consensus={analysis.consensus}
                  perImageResults={analysis.perImageResults}
                  primaryImageId={analysis.primaryImageId}
                  selectedImageId={selectedImageId}
                  onSelectImage={setSelectedImageId}
                />
              )}
              <SegmentationPanel segmentation={analysis.segmentation} />
            </>
          ) : (
            <div className="bg-bg2 border border-border rounded-rad2 p-16 text-center text-text3 text-sm">
              Run analysis to see the model's attention map and any detected lesion measurements here.
            </div>
          )}
        </div>

        {/* RIGHT — secondary quantitative detail, then the actual
            clinical deliverable (the report). Class probabilities
            support/qualify the headline banner above; the report is
            what the radiologist is actually here to produce. */}
        <div className="flex flex-col gap-5">
          {analysis ? (
            <>
              <ClassificationPanel result={analysis} />
              {report && <ReportPanel report={report} onSigned={setReport} />}
            </>
          ) : (
            <div className="bg-bg2 border border-border rounded-rad2 p-8 text-center text-text3 text-sm">
              No analysis yet for this study.
              <RoleGuard permission="run_inference">
                <div className="mt-1">Click "Run analysis" to classify and segment.</div>
              </RoleGuard>
            </div>
          )}
        </div>
      </div>

      {/* BOTTOM — reference/audit material, identical across every
          case, collapsed by default so it never competes for attention
          with the actual patient findings above. */}
      {analysis && (
        <ModelReferenceSection>
          <ModelInfoFooter result={analysis} />
          <ModelPerformanceCard />
          <ArchitectureCard />
        </ModelReferenceSection>
      )}
    </div>
  );
}
