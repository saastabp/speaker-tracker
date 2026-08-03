import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';

// Mirrors backend models/materials.py.
//
// **Bytes never travel through our API.** Uploading is a three-step dance the hooks below hide:
// ask for a presigned URL, PUT the file straight to S3, then tell the API which key it landed on.
// The server re-reads size and type from S3 rather than believing us, so there is no point sending
// them — and the size cap is enforced there, not here.

/** Largest file the API accepts. Mirrored from `storage.MAX_MATERIAL_BYTES` so the picker can fail
 *  a 40 MB file instantly instead of after a long upload that was always going to be rejected.
 *  This is a courtesy, not the enforcement — the server checks the real size after upload. */
export const MAX_MATERIAL_BYTES = 25 * 1024 * 1024;

export interface Material {
  id: number;
  talk_id: number | null;
  name: string;
  s3_key: string;
  content_type: string;
  size_bytes: number;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

interface UploadTicket {
  upload_url: string;
  s3_key: string;
  content_type: string;
}

const materialKeys = {
  all: ['materials'] as const,
  list: (talkId?: number) => ['materials', 'list', talkId ?? null] as const,
};

/** List materials; `talkId` scopes to one talk, omitted returns the whole library. */
export function useMaterials(talkId?: number): UseQueryResult<Material[]> {
  const api = useApi();
  return useQuery({
    queryKey: materialKeys.list(talkId),
    queryFn: async () =>
      (
        await api<{ materials: Material[] }>(
          `/materials${talkId ? `?talk_id=${talkId}` : ''}`,
        )
      ).materials,
  });
}

/**
 * Put a file in S3 and return the key it landed on.
 *
 * The PUT goes to the presigned URL with a bare `fetch` — deliberately not through `useApi`, which
 * attaches our bearer token. Sending our credentials to a third-party host is exactly the mistake
 * presigned URLs exist to avoid, and S3 would reject the request anyway. The content type must
 * match what was signed, or S3 answers 403.
 */
async function putToS3(ticket: UploadTicket, file: File): Promise<string> {
  const response = await fetch(ticket.upload_url, {
    method: 'PUT',
    body: file,
    headers: { 'Content-Type': ticket.content_type },
  });
  if (!response.ok) {
    throw new Error(`Upload failed (${response.status}). The file was not saved.`);
  }
  return ticket.s3_key;
}

/** Reject before uploading what the server would reject after. */
function assertWithinCap(file: File): void {
  if (file.size > MAX_MATERIAL_BYTES) {
    const mb = Math.round(MAX_MATERIAL_BYTES / (1024 * 1024));
    throw new Error(`“${file.name}” is larger than the ${mb} MB limit.`);
  }
}

/** Upload a new material: ticket → S3 → register. */
export function useUploadMaterial() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, talkId }: { file: File; talkId?: number | null }) => {
      assertWithinCap(file);
      const ticket = await api<UploadTicket>('/materials/upload-url', {
        method: 'POST',
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || 'application/octet-stream',
        }),
      });
      const s3Key = await putToS3(ticket, file);
      return api<Material>('/materials', {
        method: 'POST',
        body: JSON.stringify({ name: file.name, s3_key: s3Key, talk_id: talkId ?? null }),
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: materialKeys.all }),
  });
}

/**
 * Replace a material's file, keeping its id, name and talk.
 *
 * Safe to do freely: attaching a material copies its bytes into the message, so every email
 * already sent keeps the version it went out with. The UI still confirms first, because
 * overwriting is not undoable from here.
 */
export function useReplaceMaterialFile() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, file }: { id: number; file: File }) => {
      assertWithinCap(file);
      const ticket = await api<UploadTicket>('/materials/upload-url', {
        method: 'POST',
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || 'application/octet-stream',
        }),
      });
      const s3Key = await putToS3(ticket, file);
      return api<Material>(`/materials/${id}/file`, {
        method: 'PUT',
        body: JSON.stringify({ s3_key: s3Key }),
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: materialKeys.all }),
  });
}

export function useRenameMaterial() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name, talkId }: { id: number; name: string; talkId?: number | null }) =>
      api<Material>(`/materials/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ name, talk_id: talkId ?? null }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: materialKeys.all }),
  });
}

export function useDeleteMaterial() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<{ deleted: boolean }>(`/materials/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: materialKeys.all }),
  });
}

/**
 * Fetch a short-lived signed URL for one material.
 *
 * Imperative rather than a query: the URL expires in minutes, so it is asked for at the moment of
 * use — opening a preview, clicking download — instead of being cached alongside the listing and
 * going stale in place.
 */
export function useMaterialUrl() {
  const api = useApi();
  return useMutation({
    mutationFn: ({ id, download }: { id: number; download?: boolean }) =>
      api<{ url: string; expires_in: number }>(
        `/materials/${id}/url${download ? '?disposition=attachment' : ''}`,
      ),
  });
}