import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Musicbox — Your sound, your space', description: 'Your music library, audio player and personalized recommendations' };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en" dir="ltr"><body>{children}</body></html>;
}
