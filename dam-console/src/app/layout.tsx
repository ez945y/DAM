import type { Metadata } from 'next'
import './globals.css'
import { Sidebar } from '@/components/Sidebar'
import { DisplayUnitProvider } from '@/hooks/useDisplayUnit'

export const metadata: Metadata = {
  title: 'DAM Console',
  description: 'Detachable Action Monitor — Real-time Safety Console',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-dam-bg text-dam-text font-mono flex h-screen overflow-hidden">
        <DisplayUnitProvider>
          <Sidebar />
          <main className="flex-1 overflow-y-auto min-h-0">
            {children}
          </main>
        </DisplayUnitProvider>
      </body>
    </html>
  )
}
