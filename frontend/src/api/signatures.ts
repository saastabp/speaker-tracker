import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/signatures.py. body_html is the Tiptap editor's styled HTML output.

export interface SignatureInput {
  name: string;
  body_html: string;
  is_default: boolean;
}

export interface Signature extends SignatureInput {
  id: number;
  created_at: string;
  updated_at: string;
}

const signatureKeys = { all: ['signatures'] as const };

/** List the caller's signatures (default first). */
export function useSignatures(): UseQueryResult<Signature[]> {
  const api = useApi();
  return useQuery({
    queryKey: signatureKeys.all,
    queryFn: async () => (await api<{ signatures: Signature[] }>('/signatures')).signatures,
  });
}

export function useCreateSignature() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SignatureInput) =>
      api<Signature>('/signatures', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: signatureKeys.all }),
  });
}

export function useUpdateSignature(id: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SignatureInput) =>
      api<Signature>(`/signatures/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: signatureKeys.all }),
  });
}