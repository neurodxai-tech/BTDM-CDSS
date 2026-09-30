import type { Metadata } from 'next';
import { AuthProvider } from '@/lib/auth';
import './globals.css';

export const metadata: Metadata = {
  title: 'BTDM-CDSS',
  description: 'Brain Tumour Clinical Decision Support System — research build',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans bg-bg text-text min-h-screen">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
