import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

type Variant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg' | 'icon';

const variants: Record<Variant, string> = {
    primary:
        'bg-accent text-on-accent font-semibold hover:bg-accent shadow-xs hover:shadow-brand-glow',
    secondary:
        'bg-raised text-fg hover:bg-raised-2 border border-line',
    outline:
        'border border-line text-fg hover:bg-raised/60 hover:text-white',
    ghost: 'text-fg-2 hover:bg-raised/60 hover:text-white',
    danger: 'bg-severity-critical text-white hover:bg-rose-600',
};

const sizes: Record<Size, string> = {
    sm: 'h-8 px-3 text-xs rounded-md gap-1.5',
    md: 'h-10 px-4 text-sm rounded-lg gap-2',
    lg: 'h-12 px-6 text-base rounded-lg gap-2',
    icon: 'h-10 w-10 rounded-lg justify-center',
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: Variant;
    size?: Size;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant = 'primary', size = 'md', type = 'button', ...props }, ref) => (
        <button
            ref={ref}
            type={type}
            className={cn(
                'inline-flex items-center justify-center font-medium transition-all',
                'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950',
                'disabled:opacity-50 disabled:pointer-events-none',
                variants[variant],
                sizes[size],
                className,
            )}
            {...props}
        />
    ),
);
Button.displayName = 'Button';

export default Button;
