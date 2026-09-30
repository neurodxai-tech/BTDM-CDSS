'use client';

import { useEffect, useState } from 'react';
import { PASSWORD_RULES, passwordProblems } from '@/lib/types';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { changePassword } from '@/lib/api';

const inputCls =
  'bg-bg3 border border-border rounded-rad px-3 py-2.5 text-sm text-text focus:border-accent outline-none w-full';

export default function ChangePasswordPage() {
  const { user, loading, updateUser, logout } = useAuth();
  const router = useRouter();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
  }, [user, loading, router]);

  if (!user) return null;

  const mismatch = confirm.length > 0 && next !== confirm;
  const problems = next ? passwordProblems(next, user?.username) : [];
  const valid = current && next && problems.length === 0 && next === confirm;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await changePassword(current, next);
      updateUser(updated);
      router.replace(updated.role === 'admin' ? '/admin' : '/studies');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Password could not be changed.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <form onSubmit={handleSubmit} className="w-full max-w-md bg-bg2 border border-border rounded-rad2 p-6 flex flex-col gap-4">
        <div>
          <h1 className="font-serif text-2xl tracking-tight">Choose your password</h1>
          <p className="text-sm text-text2 mt-1.5 leading-relaxed">
            Welcome, {user.name}. {user.mustChangePassword
              ? 'You signed in with a temporary password. Please choose your own before continuing.'
              : 'Change your password.'}
          </p>
        </div>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-text2">{user.mustChangePassword ? 'Temporary password' : 'Current password'}</span>
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} className={inputCls} autoFocus />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-text2">New password ({PASSWORD_RULES})</span>
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-text2">Repeat new password</span>
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} className={inputCls} />
          {mismatch && <span className="text-xs text-glioma">Passwords don’t match</span>}
          {problems.length > 0 && (
            <span className="text-xs text-warn">Still needs: {problems.join(', ')}</span>
          )}
        </label>
        {error && (
          <div className="text-xs text-[#fca5a5] bg-glioma/10 border border-glioma/25 rounded-rad p-2.5">{error}</div>
        )}
        <button
          type="submit"
          disabled={saving || !valid}
          className="bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded-rad px-4 py-2.5"
        >
          {saving ? 'Saving…' : 'Save password and continue'}
        </button>
        <button type="button" onClick={logout} className="text-xs text-text3 hover:text-text2">
          Sign out
        </button>
      </form>
    </div>
  );
}
