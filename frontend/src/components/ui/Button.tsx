import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

type Variant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg' | 'icon';

// One button system for the whole app. Primary = solid accent (lavender-violet); danger stays outlined until hovered or focused.
const variants: Record<Variant, string> = {
    primary: 'bg-accent text-on-accent font-semibold hover:bg-accent-hover',
    secondary: 'bg-panel text-fg border border-line-strong hover:bg-raised',
    outline: 'border border-line-strong text-fg hover:bg-raised',
    ghost: 'text-fg-2 hover:bg-raised hover:text-fg',
    danger: 'border border-severity-critical/50 text-severity-critical hover:bg-severity-critical hover:text-white focus-visible:bg-severity-critical focus-visible:text-white',
};

const sizes: Record<Size, string> = {
    sm: 'h-8 px-3 text-xs gap-1.5',
    md: 'h-9 px-3.5 text-sm gap-2',
    lg: 'h-11 px-5 text-base gap-2',
    icon: 'h-9 w-9 justify-center',
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
                'inline-flex items-center justify-center rounded-md font-medium whitespace-nowrap transition-colors',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
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

/** Class strings for links/buttons that can't use <Button> (e.g. react-router <Link>). */
export const buttonClass = (variant: Variant = 'primary', size: Size = 'md') => cn(
    'inline-flex items-center justify-center rounded-md font-medium whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
    variants[variant], sizes[size],
);

export default Button;
