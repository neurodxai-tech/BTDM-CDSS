'use client';

import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import type { Role, User } from './types';
import { login as apiLogin, logout as apiLogout } from './api';

// ── AUTH ─────────────────────────────────────────────────────────────────
// Real credential-based login against the backend (app/services/auth.py):
// username/password -> server-issued bearer token, stored in
// localStorage and sent as `Authorization: Bearer <token>` on
// authenticated requests (see lib/api.ts's signReport/deleteStudy/user
// management functions). This replaced an earlier demo-role-picker mock.
//
// Still not real OIDC (spec §7) — swap this provider for that when
// you're ready; the rest of the app only depends on AuthContextValue's
// shape below.

const TOKEN_KEY = 'btdm_auth_token';
const USER_KEY = 'btdm_auth_user';
// Matches the server's SESSION_IDLE_MINUTES. The browser signs out on its own
// so an unattended screen does not keep showing a doctor's patients.
const IDLE_LIMIT_MS = Number(process.env.NEXT_PUBLIC_SESSION_IDLE_MINUTES ?? '30') * 60 * 1000;

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  loginError: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
  can: (permission: Permission) => boolean;
  updateUser: (user: User) => void;
}

export type Permission =
  | 'view_study'
  | 'run_inference'
  | 'sign_report'
  | 'manage_users'
  | 'manage_models'
  | 'upload_study'
  | 'delete_study';

// Fine-grained RBAC matrix — enforced here for UI gating, and MUST also be
// enforced server-side (this file governs rendering only — the backend's
// app/deps.py::require_role is the actual security boundary).
const PERMISSIONS: Record<Role, Permission[]> = {
  radiologist: ['view_study', 'upload_study', 'run_inference', 'sign_report', 'delete_study'],
  referring_physician: ['view_study', 'upload_study', 'run_inference', 'delete_study'],
  technologist: ['view_study', 'upload_study', 'run_inference', 'delete_study'],
  admin: ['view_study', 'manage_users', 'manage_models'],
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loginError, setLoginError] = useState<string | null>(null);

  useEffect(() => {
    const storedToken = window.localStorage.getItem(TOKEN_KEY);
    const storedUser = window.localStorage.getItem(USER_KEY);
    if (storedToken && storedUser) {
      try {
        setToken(storedToken);
        setUser(JSON.parse(storedUser));
      } catch {
        window.localStorage.removeItem(TOKEN_KEY);
        window.localStorage.removeItem(USER_KEY);
      }
    }
    setLoading(false);
  }, []);

  const login = async (username: string, password: string): Promise<boolean> => {
    setLoginError(null);
    try {
      const { token: newToken, user: newUser } = await apiLogin(username, password);
      window.localStorage.setItem(TOKEN_KEY, newToken);
      window.localStorage.setItem(USER_KEY, JSON.stringify(newUser));
      setToken(newToken);
      setUser(newUser);
      return true;
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Login failed.');
      return false;
    }
  };

  const logout = () => {
    if (token) apiLogout(token).catch(() => {});
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  };

  // Automatic sign-out after inactivity, and in every tab when one tab signs out.
  useEffect(() => {
    if (!user) return;
    let last = Date.now();
    const touch = () => {
      last = Date.now();
    };
    const events = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart'] as const;
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const timer = window.setInterval(() => {
      if (Date.now() - last > IDLE_LIMIT_MS) {
        logout();
        window.location.href = '/login?reason=idle';
      }
    }, 15000);
    const onStorage = (e: StorageEvent) => {
      if (e.key === TOKEN_KEY && e.newValue === null) {
        setToken(null);
        setUser(null);
        window.location.href = '/login';
      }
    };
    window.addEventListener('storage', onStorage);
    return () => {
      events.forEach((e) => window.removeEventListener(e, touch));
      window.clearInterval(timer);
      window.removeEventListener('storage', onStorage);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const updateUser = (next: User) => {
    window.localStorage.setItem(USER_KEY, JSON.stringify(next));
    setUser(next);
  };

  const can = (permission: Permission) => {
    if (!user) return false;
    return PERMISSIONS[user.role].includes(permission);
  };

  return (
    <AuthContext.Provider value={{ user, token, loading, loginError, login, logout, can, updateUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
