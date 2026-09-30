'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { RoleGuard } from './RoleGuard';
import clsx from 'clsx';

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();

  if (!user) return <>{children}</>;

  const navItem = (href: string, label: string) => (
    <Link
      href={href}
      className={clsx(
        'px-3 py-2 rounded-rad text-sm transition-colors',
        pathname === href || pathname?.startsWith(href + '/')
          ? 'bg-bg4 text-text font-medium'
          : 'text-text2 hover:text-text hover:bg-bg3'
      )}
    >
      {label}
    </Link>
  );

  return (
    <div className="min-h-screen flex flex-col">
      <header className="h-[60px] flex items-center gap-5 px-7 bg-bg2 border-b border-border sticky top-0 z-50">
        <div className="w-9 h-9 rounded-rad bg-gradient-to-br from-[#1a3a8a] to-[#2563eb] flex items-center justify-center flex-shrink-0">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="white" strokeWidth="1.5">
            <path d="M9.5 2a6.5 6.5 0 0 1 5.974 9.026L21 17l-4 4-5.974-5.526A6.5 6.5 0 1 1 9.5 2z" />
            <circle cx="9.5" cy="8.5" r="2" />
          </svg>
        </div>
        <span className="font-serif text-xl tracking-tight">
          BTDM <span className="text-accent2">CDSS</span>
        </span>
        <span className="text-[0.68rem] font-mono bg-accent/15 text-accent2 border border-accent/25 rounded-full px-2.5 py-0.5">
          research build · not for clinical use
        </span>
        <nav className="flex items-center gap-1 ml-6">
          {user.role !== 'admin' && navItem('/studies', 'Worklist')}
          <RoleGuard permission="manage_users">{navItem('/admin', 'Admin')}</RoleGuard>
        </nav>
        <div className="ml-auto flex items-center gap-4 text-sm">
          <div className="text-right">
            <div className="text-text">{user.name}</div>
            <div className="text-text3 text-xs capitalize">{user.role.replace('_', ' ')}</div>
          </div>
          <button
            onClick={logout}
            className="text-text2 hover:text-text border border-border2 rounded-rad px-3 py-1.5 text-xs"
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="flex-1 p-7">{children}</main>
      <footer className="text-center text-[0.72rem] text-text3 py-3 border-t border-border">
        Research prototype. Not a cleared medical device. All predictions require radiologist review and sign-off.
      </footer>
    </div>
  );
}
