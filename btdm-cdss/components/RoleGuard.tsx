'use client';

import { ReactNode } from 'react';
import { useAuth, Permission } from '@/lib/auth';

/**
 * Renders children only if the current user has the given permission.
 * NOTE: this is a UI convenience, not a security boundary — the API layer
 * must independently enforce the same permission (spec §7). Never assume
 * hiding a button here is sufficient authorization.
 */
export function RoleGuard({
  permission,
  children,
  fallback = null,
}: {
  permission: Permission;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { can } = useAuth();
  if (!can(permission)) return <>{fallback}</>;
  return <>{children}</>;
}
