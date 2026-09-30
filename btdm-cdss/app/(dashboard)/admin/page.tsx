'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth';
import { UserManagementPanel } from '@/components/UserManagementPanel';
import { ModelRegistryPanel } from '@/components/ModelRegistryPanel';
import { RegistrationRequestsPanel } from '@/components/RegistrationRequestsPanel';



export default function AdminPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [usersVersion, setUsersVersion] = useState(0);

  useEffect(() => {
    if (!loading && user && user.role !== 'admin') router.replace('/studies');
  }, [user, loading, router]);

  if (!user || user.role !== 'admin') return null;

  return (
    <div className="flex flex-col gap-10 max-w-5xl mx-auto">
      <RegistrationRequestsPanel onApproved={() => setUsersVersion((v) => v + 1)} />
      <UserManagementPanel key={usersVersion} />

      <div className="flex flex-col gap-6">
        <h1 className="font-serif text-2xl tracking-tight">Model registry</h1>
        <ModelRegistryPanel />

      </div>
    </div>
  );
}
