import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
    invalid?: boolean;
    inputSize?: 'sm' | 'md';
}

export const fieldClass = (invalid?: boolean, size: 'sm' | 'md' = 'md') => cn(
    'w-full bg-panel border rounded-md text-fg placeholder:text-muted transition-colors',
    'focus:outline-2 focus:-outline-offset-1 focus:outline-accent',
    'disabled:opacity-50 disabled:cursor-not-allowed',
    size === 'sm' ? 'h-8 px-2.5 text-xs' : 'h-9 px-3 text-sm',
    invalid ? 'border-severity-critical' : 'border-line-strong',
);

const Input = forwardRef<HTMLInputElement, InputProps>(
    ({ className, invalid, inputSize, ...props }, ref) => (
        <input ref={ref} aria-invalid={invalid || undefined} className={cn(fieldClass(invalid, inputSize), className)} {...props} />
    ),
);
Input.displayName = 'Input';

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement> & { inputSize?: 'sm' | 'md' }>(
    ({ className, inputSize, ...props }, ref) => <select ref={ref} className={cn(fieldClass(false, inputSize), className)} {...props} />,
);
Select.displayName = 'Select';

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
    ({ className, ...props }, ref) => <textarea ref={ref} className={cn(fieldClass(), 'h-auto py-2 leading-relaxed', className)} {...props} />,
);
Textarea.displayName = 'Textarea';

export default Input;
