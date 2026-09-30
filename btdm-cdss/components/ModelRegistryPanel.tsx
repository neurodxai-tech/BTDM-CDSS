'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { activateModel, getModelRegistry, registerModel } from '@/lib/api';
import type { ModelRegistryState } from '@/lib/types';

const STATUS_COLOR: Record<string, string> = {
  validated: 'text-normal',
  pending: 'text-warn',
  failed: 'text-glioma',
};

export function ModelRegistryPanel() {
  const [state, setState] = useState<ModelRegistryState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [version, setVersion] = useState('');
  const [notes, setNotes] = useState('');
  const pendingRef = useRef(false);

  useEffect(() => {
    pendingRef.current = !!state?.models.some((m) => m.status === 'pending');
  }, [state]);

  const refresh = useCallback(() => {
    getModelRegistry()
      .then(setState)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    refresh();
    // Pending validations finish in the background; refresh while any are pending.
    const t = setInterval(() => {
      if (pendingRef.current) refresh();
    }, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const upload = async () => {
    if (!file || !version.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await registerModel(file, version.trim(), notes);
      setFile(null);
      setVersion('');
      setNotes('');
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const activate = async (id: number, v: string) => {
    if (!window.confirm(`Make ${v} the active model? Every new analysis will use it.`)) return;
    setBusy(true);
    setError(null);
    try {
      await activateModel(id);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return <div className="text-sm text-text3">{error ?? 'Loading model registry…'}</div>;
  }

  return (
    <div className="flex flex-col gap-4">
      <div
        className={
          'rounded-rad p-3 text-xs leading-relaxed border ' +
          (state.locked ? 'bg-warn/10 border-warn/25 text-warn' : 'bg-glioma/10 border-glioma/30 text-glioma')
        }
      >
        {state.locked ? (
          <>
            <span className="font-medium">Model locked.</span> The active model cannot be changed from
            here. This is required while external validation (Part 2) is running: every result must come
            from the same frozen checkpoint. New checkpoints can still be uploaded and validated.
          </>
        ) : (
          <>
            <span className="font-medium">Model unlocked.</span> Validated checkpoints can be activated.
            Do not unlock during external validation.
          </>
        )}
        <div className="mt-1 text-text3">
          Training folder:{' '}
          {state.trainingFolder ? (
            state.trainingFolder.error ? (
              <span className="text-glioma">{state.trainingFolder.error}</span>
            ) : (
              <span className="text-text2">
                watching <span className="font-mono break-all">{state.trainingFolder.folder}</span> ·{' '}
                {state.trainingFolder.checkpoint_files} checkpoint file(s) · last checked{' '}
                {new Date(state.trainingFolder.last_scan).toLocaleTimeString()} ·{' '}
                {state.locked
                  ? 'new files are imported and validated, not activated (locked)'
                  : state.autoActivate
                    ? 'newest validated file is activated automatically'
                    : 'automatic activation off'}
              </span>
            )
          ) : (
            <span>not connected</span>
          )}
        </div>
        <div className="mt-1 text-text3">
          Inference worker:{' '}
          {state.workerOnline ? (
            <span className="text-normal">
              online · serving {state.workerLoadedModel?.version ?? '—'} on {state.workerLoadedModel?.device ?? '—'}
            </span>
          ) : (
            <span className="text-glioma">offline — analyses will queue until it starts</span>
          )}
        </div>
      </div>

      <div className="bg-bg2 border border-border rounded-rad2 overflow-x-auto">
        <div className="min-w-[760px]">
          <div className="grid grid-cols-[1.3fr_1.2fr_0.8fr_1.4fr_0.8fr] gap-3 px-5 py-3 text-[0.68rem] uppercase tracking-wider text-text3 border-b border-border2 font-medium">
            <span>Version</span>
            <span>Checkpoint</span>
            <span>Validation</span>
            <span>SHA-256</span>
            <span />
          </div>
          {state.models.length === 0 && (
            <div className="px-5 py-4 text-sm text-text3">No models registered.</div>
          )}
          {state.models.map((m) => (
            <div
              key={m.id}
              className="grid grid-cols-[1.3fr_1.2fr_0.8fr_1.4fr_0.8fr] gap-3 px-5 py-3.5 text-sm border-b border-border last:border-b-0 items-center"
            >
              <span className="font-mono text-text">
                {m.version}
                {m.isActive && (
                  <span className="ml-2 text-[0.65rem] bg-accent/15 text-accent2 rounded px-1.5 py-0.5">active</span>
                )}
                <br />
                <span className="text-text3 text-xs">
                  {m.source === 'watch-folder' && m.sourcePath ? (
                    <>
                      📁 {m.sourcePath}
                      {m.sourceMtime && ` · saved ${new Date(m.sourceMtime).toLocaleString()}`}
                    </>
                  ) : (
                    `${m.source === 'bootstrap' ? 'weights/classifier.pt' : 'uploaded'} · ${new Date(m.createdAt).toLocaleString()}`
                  )}
                </span>
              </span>
              <span className={'font-mono text-xs ' + (m.checkpointId === state.expectedCheckpointId ? 'text-text2' : 'text-warn')}>
                {m.checkpointId ?? '—'}
              </span>
              <span className={STATUS_COLOR[m.status] ?? 'text-text3'} title={m.validationDetail ? JSON.stringify(m.validationDetail) : ''}>
                {m.status}
              </span>
              <span className="font-mono text-[0.7rem] text-text3 break-all">{m.sha256.slice(0, 20)}…</span>
              <span>
                {!m.isActive && m.status === 'validated' && (
                  <button
                    onClick={() => activate(m.id, m.version)}
                    disabled={busy || state.locked}
                    title={state.locked ? 'Locked — see notice above' : 'Activate'}
                    className="text-xs border border-border2 rounded-rad px-3 py-1.5 text-text2 hover:text-text disabled:opacity-40"
                  >
                    Activate
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-3">
        <div className="text-xs uppercase tracking-wider text-text3 font-medium">Register a checkpoint</div>
        <div className="flex flex-wrap gap-2 items-center">
          <input
            type="file"
            accept=".pt,.pth"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm text-text2"
          />
          <input
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            placeholder="Version, e.g. v1.1.0-seed42-recal"
            className="bg-bg border border-border2 rounded-rad px-3 py-2 text-sm flex-1 min-w-[220px]"
          />
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)"
            className="bg-bg border border-border2 rounded-rad px-3 py-2 text-sm flex-1 min-w-[180px]"
          />
          <button
            onClick={upload}
            disabled={busy || !file || !version.trim()}
            className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white rounded-rad px-4 py-2 text-sm font-medium"
          >
            {busy ? 'Uploading…' : 'Upload & validate'}
          </button>
        </div>
        <p className="text-[0.7rem] text-text3 leading-relaxed">
          The worker checks every upload before it can be used: exact weight-key match, parameter count
          (25,057,731), and a fixed-input smoke test. Every analysis records the SHA-256 of the checkpoint
          that produced it.
        </p>
        {error && <div className="text-xs text-glioma">{error}</div>}
      </div>
    </div>
  );
}
