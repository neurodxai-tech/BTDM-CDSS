'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/lib/auth';

export default function LoginPage() {
  const { login, loginError } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('reason') === 'idle') {
      setNotice('You were signed out after 30 minutes of inactivity to protect patient data.');
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    const ok = await login(username.trim(), password);
    setSubmitting(false);
    if (ok) router.push('/studies');
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-md flex flex-col gap-6">
        <div className="text-center">
          <div className="w-12 h-12 rounded-rad2 bg-gradient-to-br from-[#1a3a8a] to-[#2563eb] flex items-center justify-center mx-auto mb-4">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="white" strokeWidth="1.5">
              <path d="M9.5 2a6.5 6.5 0 0 1 5.974 9.026L21 17l-4 4-5.974-5.526A6.5 6.5 0 1 1 9.5 2z" />
              <circle cx="9.5" cy="8.5" r="2" />
            </svg>
          </div>
          <h1 className="font-serif text-3xl tracking-tight">
            BTDM <span className="text-accent2">CDSS</span>
          </h1>
          <p className="text-text3 text-sm mt-2">
            Brain Tumour Clinical Decision Support System · research build
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-bg2 border border-border rounded-rad2 p-6 flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-text2">Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              className="bg-bg3 border border-border rounded-rad px-3 py-2.5 text-sm text-text focus:border-accent outline-none"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-text2">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="bg-bg3 border border-border rounded-rad px-3 py-2.5 text-sm text-text focus:border-accent outline-none"
            />
          </label>

          {notice && !loginError && (
            <div className="text-xs text-warn bg-warn/10 border border-warn/25 rounded-rad p-2.5">{notice}</div>
          )}
          {loginError && (
            <div className="text-xs text-[#fca5a5] bg-glioma/10 border border-glioma/25 rounded-rad p-2.5">
              {loginError}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !username || !password}
            className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded-rad px-4 py-2.5 transition-colors"
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>

          <p className="text-xs text-text3 leading-relaxed pt-3 border-t border-border text-center">
            New doctor?{' '}
            <Link href="/register" className="text-accent2 hover:underline">
              Request an account
            </Link>
            . An administrator reviews each request and issues your login details.
          </p>
        </form>

        <p className="text-center text-[0.7rem] text-text3">
          Research prototype · not a cleared medical device
        </p>
      </div>
    </div>
  );
}
