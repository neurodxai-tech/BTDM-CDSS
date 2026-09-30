'use client';

import { useCallback, useEffect, useState } from 'react';
import { approveRegistration, listRegistrations, rejectRegistration } from '@/lib/api';
import type { GeneratedCredentials, RegistrationRequest } from '@/lib/types';

const ROLE_LABEL: Record<string, string> = {
  radiologist: 'Radiologist',
  referring_physician: 'Referring physician',
};

const STATUS_COLOR: Record<string, string> = {
  pending: 'text-warn',
  approved: 'text-normal',
  rejected: 'text-glioma',
};

export function RegistrationRequestsPanel({ onApproved }: { onApproved?: () => void }) {
  const [requests, setRequests] = useState<RegistrationRequest[] | null>(null);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [roleOverride, setRoleOverride] = useState<Record<string, string>>({});
  const [creds, setCreds] = useState<GeneratedCredentials | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    setError(null);
    listRegistrations(filter === 'pending' ? 'pending' : undefined)
      .then(setRequests)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [filter]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000); // pick up new submissions
    return () => clearInterval(t);
  }, [load]);

  const approve = async (r: RegistrationRequest) => {
    const role = roleOverride[r.id] ?? r.requestedRole;
    if (!window.confirm(`Approve ${r.fullName} as ${ROLE_LABEL[role]}? An account will be created.`)) return;
    setBusyId(r.id);
    setError(null);
    try {
      const result = await approveRegistration(r.id, role);
      setCreds(result);
      setCopied(false);
      onApproved?.();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const reject = async (r: RegistrationRequest) => {
    const reason = window.prompt(`Reject the registration from ${r.fullName}? Optional reason:`, '');
    if (reason === null) return;
    setBusyId(r.id);
    setError(null);
    try {
      await rejectRegistration(r.id, reason);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const credsText = creds
    ? `BTDM-CDSS login for ${creds.registration.fullName}\n` +
      `Address: ${typeof window !== 'undefined' ? window.location.origin : ''}\n` +
      `Username: ${creds.username}\nTemporary password: ${creds.temporaryPassword}\n` +
      `You will be asked to choose your own password at first sign-in.`
    : '';

  const pendingCount = requests?.filter((r) => r.status === 'pending').length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="font-serif text-2xl tracking-tight">Doctor registrations</h1>
        {filter === 'pending' && pendingCount > 0 && (
          <span className="text-xs bg-warn/15 text-warn border border-warn/25 rounded-full px-2.5 py-0.5">
            {pendingCount} waiting
          </span>
        )}
        <div className="ml-auto flex gap-1 text-xs">
          {(['pending', 'all'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={
                'px-3 py-1.5 rounded-rad border ' +
                (filter === f ? 'bg-bg4 border-border2 text-text' : 'border-border text-text3 hover:text-text2')
              }
            >
              {f === 'pending' ? 'Pending' : 'All'}
            </button>
          ))}
        </div>
      </div>

      {creds && (
        <div className="bg-normal/10 border border-normal/30 rounded-rad2 p-5 flex flex-col gap-3">
          <div className="text-sm text-normal font-medium">
            Account created for {creds.registration.fullName}
          </div>
          <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <span className="text-text3">Username</span>
            <span className="font-mono text-text">{creds.username}</span>
            <span className="text-text3">Temporary password</span>
            <span className="font-mono text-text">{creds.temporaryPassword}</span>
          </div>
          <p className="text-xs text-text2 leading-relaxed">
            This password is shown <span className="text-text font-medium">only once</span> and is not
            stored. Give it to the doctor in person or through a secure channel. They must choose their
            own password at first sign-in.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => {
                navigator.clipboard?.writeText(credsText).then(() => setCopied(true)).catch(() => {});
              }}
              className="text-xs border border-border2 rounded-rad px-3 py-1.5 text-text2 hover:text-text"
            >
              {copied ? 'Copied ✓' : 'Copy login details'}
            </button>
            <button
              onClick={() => setCreds(null)}
              className="text-xs border border-border2 rounded-rad px-3 py-1.5 text-text2 hover:text-text"
            >
              Done — hide password
            </button>
          </div>
        </div>
      )}

      {error && <div className="text-xs text-glioma">{error}</div>}

      <div className="bg-bg2 border border-border rounded-rad2 overflow-hidden">
        {requests === null && <div className="px-5 py-4 text-sm text-text3">Loading…</div>}
        {requests?.length === 0 && (
          <div className="px-5 py-6 text-sm text-text3 text-center">
            {filter === 'pending' ? 'No registrations waiting for review.' : 'No registrations yet.'}
          </div>
        )}
        {requests?.map((r) => (
          <div key={r.id} className="px-5 py-4 border-b border-border last:border-b-0 flex flex-col gap-2">
            <div className="flex items-start gap-3 flex-wrap">
              <div className="flex-1 min-w-[220px]">
                <div className="text-text font-medium">{r.fullName}</div>
                <div className="text-xs text-text3 mt-0.5">
                  {r.specialty} · {r.institution}
                </div>
              </div>
              <div className="text-xs text-text3 text-right">
                <div className="font-mono">{r.id}</div>
                <div>{new Date(r.submittedAt).toLocaleString()}</div>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs">
              <span><span className="text-text3">NMC no. </span><span className="font-mono text-text2">{r.nmcNumber}</span></span>
              <span><span className="text-text3">Email </span><span className="text-text2 break-all">{r.email}</span></span>
              <span><span className="text-text3">Phone </span><span className="text-text2">{r.phone}</span></span>
            </div>
            {r.notes && <div className="text-xs text-text2 bg-bg3 rounded-rad p-2">{r.notes}</div>}

            {r.status === 'pending' ? (
              <div className="flex items-center gap-2 flex-wrap pt-1">
                <select
                  value={roleOverride[r.id] ?? r.requestedRole}
                  onChange={(e) => setRoleOverride((m) => ({ ...m, [r.id]: e.target.value }))}
                  className="bg-bg3 border border-border rounded-rad px-2 py-1.5 text-xs text-text"
                >
                  <option value="radiologist">Radiologist</option>
                  <option value="referring_physician">Referring physician</option>
                </select>
                <button
                  onClick={() => approve(r)}
                  disabled={busyId === r.id}
                  className="text-xs bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white rounded-rad px-3 py-1.5"
                >
                  Approve & create account
                </button>
                <button
                  onClick={() => reject(r)}
                  disabled={busyId === r.id}
                  className="text-xs border border-border2 rounded-rad px-3 py-1.5 text-text2 hover:text-glioma disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            ) : (
              <div className="text-xs">
                <span className={STATUS_COLOR[r.status]}>{r.status}</span>
                {r.username && <span className="text-text3"> · username <span className="font-mono text-text2">{r.username}</span></span>}
                {r.decisionNote && <span className="text-text3"> · {r.decisionNote}</span>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
