import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
    invalid?: boolean;
}

const Input = forwardRef<HTMLInputElement, InputProps>(
    ({ className, invalid, ...props }, ref) => (
        <input
            ref={ref}
            aria-invalid={invalid || undefined}
            className={cn(
                'w-full bg-slate-950 border rounded-lg px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500',
                'transition-colors focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent',
                'disabled:opacity-50 disabled:cursor-not-allowed',
                invalid ? 'border-severity-critical' : 'border-slate-800',
                className,
            )}
            {...props}
        />
    ),
);
Input.displayName = 'Input';

export default Input;
