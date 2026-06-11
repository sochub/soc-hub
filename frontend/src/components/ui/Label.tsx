import { cn } from '../../lib/utils';

export default function Label({
    className,
    ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
    return (
        <label
            className={cn('text-sm font-medium text-slate-300', className)}
            {...props}
        />
    );
}
