import { cn } from '../../lib/utils';

export default function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
    return <label className={cn('block text-xs font-medium text-fg-2 mb-1.5', className)} {...props} />;
}
