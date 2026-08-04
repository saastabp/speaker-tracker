import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useApi } from './client';
import { dashboardKeys } from './dashboard';

// Mirrors backend models/appointments.py. `scheduled_at` is a **naive wall-clock** timestamp
// ('2026-08-07T14:00:00', no offset): the column is a DATETIME and 2pm means 2pm, so no layer
// converts it. That is also why an `<input type="datetime-local">` value goes over the wire as-is —
// its format is exactly this contract, and adding an offset would claim a precision the feature
// does not have.

export type AppointmentScope = 'upcoming' | 'past' | 'all';

export interface AppointmentInput {
  contact_id: number;
  title: string;
  scheduled_at: string; // YYYY-MM-DDTHH:mm, wall clock
  details?: string | null;
}

/** A partial edit. Omitting a key leaves it unchanged; sending `details: null` **clears** it. */
export interface AppointmentPatch {
  contact_id?: number;
  title?: string;
  scheduled_at?: string;
  details?: string | null;
}

export interface Appointment {
  id: number;
  contact_id: number;
  contact_name: string;
  title: string;
  scheduled_at: string;
  details: string | null;
  created_at: string;
}

export interface AppointmentFilters {
  /** Omitted means every appointment — the server's unfiltered default. */
  scope?: AppointmentScope;
  contactId?: number;
}

export const appointmentKeys = {
  all: ['appointments'] as const,
  list: (filters: AppointmentFilters = {}) => ['appointments', filters] as const,
};

function toQuery(filters: AppointmentFilters): string {
  const params = new URLSearchParams();
  if (filters.scope) params.set('scope', filters.scope);
  if (filters.contactId !== undefined) params.set('contact_id', String(filters.contactId));
  const query = params.toString();
  return query ? `?${query}` : '';
}

/** List appointments — soonest first, except `past`, which reads backwards from now. */
export function useAppointments(filters: AppointmentFilters = {}): UseQueryResult<Appointment[]> {
  const api = useApi();
  return useQuery({
    queryKey: appointmentKeys.list(filters),
    queryFn: async () =>
      (await api<{ appointments: Appointment[] }>(`/appointments${toQuery(filters)}`)).appointments,
  });
}

/** Every write refreshes the lists **and** the dashboard, whose "Coming up" card is served by these
 *  same rows. Invalidating the whole `appointments` key rather than one filter is deliberate: a row
 *  shows on the page, in a contact panel, and under two different scopes, and working out which of
 *  those an edit touched is more ways to be wrong than a refetch of a small list is worth. */
function useAppointmentInvalidation() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: appointmentKeys.all });
    queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
  };
}

/** Log an appointment. */
export function useCreateAppointment() {
  const api = useApi();
  const invalidate = useAppointmentInvalidation();
  return useMutation({
    mutationFn: (data: AppointmentInput) =>
      api<Appointment>('/appointments', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: invalidate,
  });
}

/** Edit an appointment — person, title, time or details. */
export function usePatchAppointment() {
  const api = useApi();
  const invalidate = useAppointmentInvalidation();
  return useMutation({
    mutationFn: ({ id, ...patch }: AppointmentPatch & { id: number }) =>
      api<Appointment>(`/appointments/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    onSuccess: invalidate,
  });
}

/** Delete an appointment. */
export function useDeleteAppointment() {
  const api = useApi();
  const invalidate = useAppointmentInvalidation();
  return useMutation({
    mutationFn: ({ id }: { id: number }) =>
      api<{ deleted: boolean }>(`/appointments/${id}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  });
}