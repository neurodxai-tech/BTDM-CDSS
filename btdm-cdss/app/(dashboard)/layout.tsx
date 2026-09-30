'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { AppShell } from '@/components/AppShell';

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
    else if (!loading && user?.mustChangePassword) router.replace('/change-password');
  }, [user, loading, router]);

  if (loading || !user || user.mustChangePassword) {
    return <div className="min-h-screen flex items-center justify-center text-text3">Loading…</div>;
  }

  return <AppShell>{children}</AppShell>;
}
