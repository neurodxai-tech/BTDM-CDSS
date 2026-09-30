'use client';

import { useState } from 'react';
import Link from 'next/link';
import { submitRegistration } from '@/lib/api';
import type { DoctorRole, RegistrationForm } from '@/lib/types';

const EMPTY: RegistrationForm = {
  fullName: '',
  email: '',
  phone: '',
  nmcNumber: '',
  specialty: '',
  requestedRole: 'radiologist',
  institution: '',
  notes: '',
  website: '',
};

const inputCls =
  'bg-bg3 border border-border rounded-rad px-3 py-2.5 text-sm text-text focus:border-accent outline-none w-full';

export default function RegisterPage() {
  const [form, setForm] = useState<RegistrationForm>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reference, setReference] = useState<string | null>(null);

  const set = (k: keyof RegistrationForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const required: (keyof RegistrationForm)[] = ['fullName', 'email', 'phone', 'nmcNumber', 'specialty', 'institution'];
  const complete = required.every((k) => String(form[k]).trim().length > 0);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      setReference(await submitRegistration(form));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration could not be submitted.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-xl flex flex-col gap-6">
        <div className="text-center">
          <h1 className="font-serif text-3xl tracking-tight">
            BTDM <span className="text-accent2">CDSS</span>
          </h1>
          <p className="text-text3 text-sm mt-2">Doctor registration · research build</p>
        </div>

        {reference ? (
          <div className="bg-bg2 border border-border rounded-rad2 p-6 flex flex-col gap-4 text-sm">
            <div className="text-normal font-medium">Registration submitted</div>
            <p className="text-text2 leading-relaxed">
              Your request is now waiting for an administrator to review it. Once it is approved,
              the administrator will give you your username and a temporary password. You will be
              asked to choose your own password the first time you sign in.
            </p>
            <div className="bg-bg3 border border-border rounded-rad p-3">
              <div className="text-xs text-text3">Your reference number</div>
              <div className="font-mono text-lg text-text mt-0.5">{reference}</div>
              <div className="text-xs text-text3 mt-1">Keep this to follow up on your request.</div>
            </div>
            <Link href="/login" className="text-accent2 hover:underline text-center">
              Back to sign in
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="bg-bg2 border border-border rounded-rad2 p-6 flex flex-col gap-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label className="flex flex-col gap-1.5 sm:col-span-2">
                <span className="text-xs text-text2">Full name (with title) *</span>
                <input value={form.fullName} onChange={set('fullName')} placeholder="Dr. Firstname Lastname" className={inputCls} autoFocus />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-text2">Email *</span>
                <input type="email" value={form.email} onChange={set('email')} className={inputCls} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-text2">Phone *</span>
                <input value={form.phone} onChange={set('phone')} placeholder="+977 98…" className={inputCls} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-text2">NMC registration number *</span>
                <input value={form.nmcNumber} onChange={set('nmcNumber')} className={inputCls} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-text2">Specialty *</span>
                <input value={form.specialty} onChange={set('specialty')} placeholder="e.g. Radiology" className={inputCls} />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-text2">Role requested *</span>
                <select
                  value={form.requestedRole}
                  onChange={(e) => setForm((f) => ({ ...f, requestedRole: e.target.value as DoctorRole }))}
                  className={inputCls}
                >
                  <option value="radiologist">Radiologist (reviews and signs reports)</option>
                  <option value="referring_physician">Referring physician (views results)</option>
                </select>
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs text-text2">Hospital / institution *</span>
                <input value={form.institution} onChange={set('institution')} className={inputCls} />
              </label>
              <label className="flex flex-col gap-1.5 sm:col-span-2">
                <span className="text-xs text-text2">Notes for the administrator (optional)</span>
                <textarea value={form.notes} onChange={set('notes')} rows={3} className={inputCls} />
              </label>
              {/* Honeypot: hidden from people, filled in by bots. */}
              <input
                type="text"
                tabIndex={-1}
                autoComplete="off"
                aria-hidden="true"
                value={form.website}
                onChange={set('website')}
                className="hidden"
              />
            </div>

            {error && (
              <div className="text-xs text-[#fca5a5] bg-glioma/10 border border-glioma/25 rounded-rad p-2.5">{error}</div>
            )}

            <button
              type="submit"
              disabled={submitting || !complete}
              className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded-rad px-4 py-2.5 transition-colors"
            >
              {submitting ? 'Submitting…' : 'Submit registration'}
            </button>

            <p className="text-xs text-text3 text-center">
              Already have an account?{' '}
              <Link href="/login" className="text-accent2 hover:underline">
                Sign in
              </Link>
            </p>
          </form>
        )}
        <p className="text-center text-[0.7rem] text-text3">Research prototype · not a cleared medical device</p>
      </div>
    </div>
  );
}
