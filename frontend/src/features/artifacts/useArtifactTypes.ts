import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';

export interface ArtifactTypeDef {
    id: number;
    key: string;
    label: string;
    show_in_mindmap: boolean;
    private: boolean;
    payload_key: string | null;
}

export const BUILTIN_TYPES = [
    { value: 'file_hash', label: 'File Hash (MD5/SHA1/SHA256)' },
    { value: 'ip', label: 'IP Address' },
    { value: 'domain', label: 'Domain' },
    { value: 'url', label: 'URL' },
    { value: 'email', label: 'Email Address' },
    { value: 'other', label: 'Other' },
];

/** Built-in + tenant custom artifact types. `options` excludes private types (UI dropdowns);
 *  `allKeys` includes them (workflow editor). */
export function useArtifactTypes() {
    const { data: custom = [] } = useQuery({
        queryKey: ['artifact-types'],
        queryFn: async () => (await api.get('/artifact-types/')).data as ArtifactTypeDef[],
        staleTime: 60_000,
    });
    const options = [...BUILTIN_TYPES, ...custom.filter((d) => !d.private).map((d) => ({ value: d.key, label: d.label }))];
    const allKeys = [...BUILTIN_TYPES.map((t) => t.value), ...custom.map((d) => d.key)];
    return { custom, options, allKeys };
}
