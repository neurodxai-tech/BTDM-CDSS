'use client';

import { useEffect, useState } from 'react';
import { useAuth } from '@/lib/auth';
import { listUsers, createUser, setUserActive, deleteUser } from '@/lib/api';
import type { Role, User } from '@/lib/types';
import { PASSWORD_RULES, passwordProblems } from '@/lib/types';

const ROLES: Role[] = ['radiologist', 'referring_physician', 'technologist', 'admin'];

function randomPassword(): string {
  // 8 cryptographically random characters with upper case, lower case and a
  // digit, no look-alikes (0/O, 1/l/I). Temporary: changed at first login.
  const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  for (;;) {
    const bytes = new Uint32Array(8);
    window.crypto.getRandomValues(bytes);
    const pw = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('');
    if (/\d/.test(pw) && /[A-Z]/.test(pw) && /[a-z]/.test(pw)) return pw;
  }
}

export function UserManagementPanel() {
  const { token, user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const [name, setName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('technologist');
  const [institution, setInstitution] = useState('');
  const [creating, setCreating] = useState(false);
  const [createdCreds, setCreatedCreds] = useState<{ username: string; password: string } | null>(null);

  const load = async () => {
    if (!token) return;
    setError(null);
    try {
      setUsers(await listUsers(token));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users.');
    }
  };

  useEffect(() => {
    load();
  }, [token]);

  const handleCreate = async () => {
    if (!token) return;
    setCreating(true);
    setError(null);
    try {
      await createUser(token, { name, username, password, role, institution: institution || '—' });
      setCreatedCreds({ username, password });
      setName('');
      setUsername('');
      setPassword('');
      setInstitution('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create user.');
    } finally {
      setCreating(false);
    }
  };

  const handleToggleActive = async (u: User) => {
    if (!token) return;
    try {
      await setUserActive(token, u.id, !u.active);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update user.');
    }
  };

  const handleDelete = async (u: User) => {
    if (!token) return;
    try {
      await deleteUser(token, u.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete user.');
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-xl tracking-tight">User management</h2>
        <button
          onClick={() => {
            setShowCreate((s) => !s);
            setCreatedCreds(null);
          }}
          className="bg-accent hover:bg-[#2563eb] text-white rounded-rad px-4 py-2 text-sm font-medium transition-colors"
        >
          {showCreate ? 'Cancel' : '+ Create user'}
        </button>
      </div>

      {error && (
        <div className="text-xs text-[#fca5a5] bg-glioma/10 border border-glioma/25 rounded-rad p-2.5">
          {error}
        </div>
      )}

      {showCreate && (
        <div className="bg-bg2 border border-border rounded-rad2 p-5 flex flex-col gap-3">
          {createdCreds ? (
            <div className="bg-normal/10 border border-normal/25 rounded-rad p-3.5 flex flex-col gap-1.5">
              <div className="text-sm text-normal font-medium">Account created</div>
              <div className="text-xs text-text2">
                Username: <span className="font-mono text-text">{createdCreds.username}</span>
              </div>
              <div className="text-xs text-text2">
                Password: <span className="font-mono text-text">{createdCreds.password}</span>
              </div>
              <div className="text-[0.68rem] text-text3 mt-1">
                Share this with them now — it isn't shown again. Create another below, or close this panel.
              </div>
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text2">Full name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text2">Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text2">Role</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r.replace('_', ' ')}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text2">Institution</span>
              <input
                value={institution}
                onChange={(e) => setInstitution(e.target.value)}
                placeholder="—"
                className="bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none"
              />
            </label>
            <label className="flex flex-col gap-1.5 col-span-2">
              <span className="text-xs text-text2">Temporary password ({PASSWORD_RULES})</span>
              <div className="flex gap-2">
                <input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="flex-1 bg-bg3 border border-border rounded-rad px-3 py-2 text-sm text-text focus:border-accent outline-none font-mono"
                />
                <button
                  type="button"
                  onClick={() => setPassword(randomPassword())}
                  className="text-xs text-accent2 border border-accent/30 rounded-rad px-3 hover:bg-accent/10"
                >
                  Generate
                </button>
              </div>
            </label>
          </div>

          <button
            onClick={handleCreate}
            disabled={creating || !name || !username || passwordProblems(password, username).length > 0}
            className="self-start bg-accent hover:bg-[#2563eb] disabled:opacity-50 text-white text-sm font-medium rounded-rad px-5 py-2 transition-colors"
          >
            {creating ? 'Creating…' : 'Create account'}
          </button>
        </div>
      )}

      <div className="bg-bg2 border border-border rounded-rad2 overflow-hidden">
        <div className="grid grid-cols-[1fr_1fr_120px_1fr_80px_140px] gap-2 px-5 py-3 text-[0.68rem] uppercase tracking-wider text-text3 border-b border-border2 font-medium">
          <span>Name</span>
          <span>Username</span>
          <span>Role</span>
          <span>Institution</span>
          <span>Status</span>
          <span></span>
        </div>
        {users === null ? (
          <div className="px-5 py-8 text-center text-text3 text-sm">Loading…</div>
        ) : (
          users.map((u) => (
            <div
              key={u.id}
              className="grid grid-cols-[1fr_1fr_120px_1fr_80px_140px] gap-2 px-5 py-3 text-sm border-b border-border last:border-b-0 items-center"
            >
              <span className="text-text">{u.name}</span>
              <span className="font-mono text-text2">{u.username}</span>
              <span className="text-text2 capitalize">{u.role.replace('_', ' ')}</span>
              <span className="text-text3">{u.institution}</span>
              <span className={u.active ? 'text-normal' : 'text-text3'}>{u.active ? 'Active' : 'Disabled'}</span>
              <div className="flex gap-1.5 justify-end">
                <button
                  onClick={() => handleToggleActive(u)}
                  disabled={u.id === currentUser?.id}
                  className="text-[0.7rem] text-text3 hover:text-text border border-border2 rounded px-2 py-1 disabled:opacity-30"
                >
                  {u.active ? 'Disable' : 'Enable'}
                </button>
                <button
                  onClick={() => handleDelete(u)}
                  disabled={u.id === currentUser?.id}
                  className="text-[0.7rem] text-text3 hover:text-[#fca5a5] border border-border2 rounded px-2 py-1 disabled:opacity-30"
                >
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
