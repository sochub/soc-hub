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
                'w-full bg-canvas border rounded-lg px-3 py-2 text-sm text-fg placeholder:text-fg-3',
                'transition-colors focus:outline-hidden focus:ring-2 focus:ring-accent focus:border-transparent',
                'disabled:opacity-50 disabled:cursor-not-allowed',
                invalid ? 'border-severity-critical' : 'border-line',
                className,
            )}
            {...props}
        />
    ),
);
Input.displayName = 'Input';

export default Input;
