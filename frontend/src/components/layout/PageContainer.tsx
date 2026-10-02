import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

/** Standard padded, width-capped wrapper for every normal routed page. */
export default function PageContainer({ children, width = 'default', className }:
    { children: ReactNode; width?: 'default' | 'narrow' | 'wide'; className?: string }) {
    const max = width === 'narrow' ? 'max-w-[1000px]' : width === 'wide' ? 'max-w-[1600px]' : 'max-w-[1400px]';
    return <div className={cn('px-4 sm:px-6 py-5 sm:py-6 mx-auto w-full space-y-6', max, className)}>{children}</div>;
}
